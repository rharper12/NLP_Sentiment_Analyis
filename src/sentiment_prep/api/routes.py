"""All endpoints, grouped by tag so Swagger reads as the workflow: collect, clean, label, export.

Sync handlers run in FastAPI's threadpool, which is what SQLAlchemy sessions and boto3 expect.
The one async handler, ``load_dataset``, is async so it can notice a client disconnect and stop
a paid X fetch at the next page boundary. Checkpoints are written the moment paid or expensive
data exists: after collect, after preprocessing, and after every labelling call.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from sentiment_prep import __version__
from sentiment_prep.api import deps
from sentiment_prep.api.schemas import (
    CheckpointList,
    ComprehendLabelRequest,
    DatasetSummary,
    HealthResponse,
    HistoryRun,
    LoadRequest,
    ManualLabelRequest,
    PreprocessRequest,
    PreprocessResponse,
    RecordPage,
    RecordPair,
    ReviewPage,
    ReviewRequest,
    SaveResponse,
    SpendSummary,
    StepInfo,
)
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import AppError, ConfigurationError, NotFoundError, ValidationError
from sentiment_prep.export.csv_export import to_csv
from sentiment_prep.export.excel_export import to_excel
from sentiment_prep.export.parquet_export import csv_to_parquet, to_parquet
from sentiment_prep.history import db
from sentiment_prep.history import services as history
from sentiment_prep.labeling import service as labeling
from sentiment_prep.labeling.service import LabelEstimate, LabelProgress, ManualLabel
from sentiment_prep.logging_config import bind_context, get_logger
from sentiment_prep.models import Dataset, DatasetBundle, LabelSummary
from sentiment_prep.preprocessing import DEFAULT_ORDER
from sentiment_prep.report import load_rationale, render_report
from sentiment_prep.sources.csv_upload import CsvUploadSource
from sentiment_prep.storage.checkpoints import (
    CheckpointInfo,
    CheckpointStore,
    Stage,
    checkpoint_bundle,
)
from sentiment_prep.storage.repository import BundleRepository

router = APIRouter()
logger = get_logger(__name__)

PREVIEW_ROWS = 20
MIN_RECORDS_FOR_TASK = 500

SettingsDep = Annotated[Settings, Depends(get_settings)]
RepoDep = Annotated[BundleRepository, Depends(deps.get_repository)]
CheckpointDep = Annotated[CheckpointStore, Depends(deps.get_checkpoint_store)]


# --- system -----------------------------------------------------------------------------------


@router.get("/health", tags=["system"], response_model=HealthResponse, summary="Liveness check")
def health(settings: SettingsDep) -> HealthResponse:
    """Liveness and, only with diagnostics on, the facts an operator checks first.

    Never touches paid services. Operator-only fields are null unless diagnostics are enabled so
    a public deployment reveals nothing about its storage or which paid services are switched on.
    """
    diagnostics = settings.diagnostics_enabled
    return HealthResponse(
        status="ok",
        version=__version__,
        diagnostics=diagnostics,
        x_configured=bool(settings.x_bearer_token or settings.x_bearer_token_ssm_path),
        runtime=settings.runtime if diagnostics else None,
        database=db.backend_name() if diagnostics else None,
        database_ephemeral=db.is_ephemeral() if diagnostics else None,
        checkpoints=deps.checkpoint_location() if diagnostics else None,
        comprehend_enabled=settings.comprehend_enabled if diagnostics else None,
        bedrock_enabled=settings.bedrock_enabled if diagnostics else None,
    )


# --- dataset ----------------------------------------------------------------------------------


@router.post(
    "/dataset/load",
    tags=["dataset"],
    response_model=DatasetSummary,
    summary="Fetch from X or Hugging Face",
)
async def load_dataset(
    body: LoadRequest,
    request: Request,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
) -> DatasetSummary:
    """Fetch up to ``limit`` records.

    X fetches are billed per post and capped (``X_MAX_READS_PER_FETCH``, ``X_MAX_READS_PER_DAY``).
    If the client disconnects mid-fetch the paging loop stops at the next page boundary so no
    further reads are billed. The ``collected`` checkpoint is written before returning.
    """
    bind_context(source=body.source, limit=body.limit)
    source: Any = (
        deps.get_x_source(query=body.query or "") if body.source == "x" else deps.get_hf_source()
    )
    stop = threading.Event()

    async def watch_disconnect() -> None:
        while not stop.is_set():
            if await request.is_disconnected():
                logger.warning("client_disconnected_during_fetch")
                stop.set()
                return
            await anyio.sleep(0.25)

    async with anyio.create_task_group() as group:
        group.start_soon(watch_disconnect)
        try:
            dataset = await anyio.to_thread.run_sync(
                lambda: source.fetch(limit=body.limit, query=body.query, should_stop=stop.is_set)
            )
        finally:
            stop.set()

    cost = (
        round(len(dataset.records) * settings.x_cost_per_read_usd, 4)
        if body.source == "x"
        else None
    )
    # Database and checkpoint writes are synchronous; keep them off the event loop.
    return await anyio.to_thread.run_sync(
        lambda: _store(dataset, repo, checkpoints, estimated_cost_usd=cost)
    )


@router.post(
    "/dataset/upload", tags=["dataset"], response_model=DatasetSummary, summary="Upload a CSV"
)
async def upload_dataset(
    repo: RepoDep,
    checkpoints: CheckpointDep,
    file: Annotated[UploadFile, File()],
    limit: int = 5000,
) -> DatasetSummary:
    """CSV with a ``text`` column and optional ``label``/``id`` columns."""
    content = await file.read()
    bind_context(source="csv", filename=file.filename, bytes=len(content))
    return await anyio.to_thread.run_sync(
        lambda: _store(CsvUploadSource(content).fetch(limit=limit), repo, checkpoints)
    )


@router.get(
    "/dataset/{dataset_id}",
    tags=["dataset"],
    response_model=DatasetSummary,
    summary="Dataset summary",
)
def get_dataset(dataset_id: str, repo: RepoDep) -> DatasetSummary:
    """Summary and preview of a previously loaded dataset."""
    return _summary(repo.get(dataset_id))


@router.get(
    "/dataset/{dataset_id}/records",
    tags=["dataset"],
    response_model=RecordPage,
    summary="Page through records",
)
def get_records(
    dataset_id: str,
    repo: RepoDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: str | None = Query(None, description="Case-insensitive substring on original text"),
) -> RecordPage:
    """Original records joined with their processed version (null if dropped)."""
    bundle = repo.get(dataset_id)
    processed = {r.id: r for r in bundle.processed.records} if bundle.processed else {}
    originals = bundle.original.records
    if search:
        needle = search.lower()
        originals = [r for r in originals if needle in r.text.lower()]
    page = originals[offset : offset + limit]
    return RecordPage(
        total=len(originals),
        offset=offset,
        items=[RecordPair(original=r, processed=processed.get(r.id)) for r in page],
    )


# --- preprocess -------------------------------------------------------------------------------


@router.get(
    "/steps", tags=["preprocess"], response_model=list[StepInfo], summary="List available steps"
)
def list_steps() -> list[StepInfo]:
    """Catalogue of steps with their human-written rationale, in the recommended order."""
    rationale = load_rationale()
    return [StepInfo(name=name, **rationale[name]) for name in DEFAULT_ORDER]


@router.post(
    "/dataset/{dataset_id}/preprocess",
    tags=["preprocess"],
    response_model=PreprocessResponse,
    summary="Run preprocessing and measure impact",
)
def preprocess(
    dataset_id: str,
    request: PreprocessRequest,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
) -> PreprocessResponse:
    """Apply steps in order. Always re-runs from the original data, so toggles are idempotent.

    Comprehend, Titan embeddings and Bedrock are best-effort: if any is disabled or fails the
    corresponding report field is null and ``report.warnings`` says why. The ``processed``
    checkpoint is rewritten on every run.
    """
    bind_context(dataset_id=dataset_id, steps=request.steps)
    bundle = repo.get(dataset_id)
    updated, before, after = run_preprocessing(
        bundle, request, settings, deps.get_comprehend_client(), deps.get_bedrock_client()
    )
    updated = checkpoint_bundle(checkpoints, updated, "processed")
    repo.save(updated)
    history.record_run(updated)
    if updated.processed is None or updated.report is None:  # pragma: no cover - invariant
        raise AppError("preprocessing produced no result")
    return PreprocessResponse(
        dataset_id=dataset_id,
        applied_steps=updated.applied_steps,
        record_count=len(updated.processed.records),
        metrics_before=before,
        metrics_after=after,
        report=updated.report,
        preview=updated.processed.records[:PREVIEW_ROWS],
    )


# --- label ------------------------------------------------------------------------------------


@router.get(
    "/dataset/{dataset_id}/labels/summary",
    tags=["label"],
    response_model=LabelSummary,
    summary="Label coverage and agreement",
)
def label_summary(dataset_id: str, repo: RepoDep) -> LabelSummary:
    """Counts by source and label, review progress, reviewer-vs-Comprehend agreement."""
    return labeling.summary(repo.get(dataset_id))


@router.get(
    "/dataset/{dataset_id}/labels/estimate",
    tags=["label"],
    response_model=LabelEstimate,
    summary="Comprehend cost estimate",
)
def label_estimate(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> LabelEstimate:
    """Billable units, and dollars when the current rate is known.

    The rate comes from the AWS Price List API (cached 24 h). If it cannot be fetched and nothing
    is cached, ``estimated_cost_usd`` is null and ``price_status`` is ``unavailable``; the UI then
    asks the person to price the job by hand rather than showing a guessed figure. Free: no
    Comprehend call is made.
    """
    return labeling.estimate(repo.get(dataset_id), settings, deps.get_comprehend_rate())


@router.post(
    "/dataset/{dataset_id}/labels/comprehend",
    tags=["label"],
    response_model=LabelProgress,
    summary="Label a slice with Comprehend",
)
def label_comprehend(
    dataset_id: str,
    request: ComprehendLabelRequest,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
) -> LabelProgress:
    """Send up to ``max_records`` unlabelled records to Comprehend; call again until ``done``.

    Resumable: records that already have a Comprehend label are skipped, so a crash or a
    cancelled loop never re-bills. After each call the bundle is saved and the ``labelled``
    checkpoint is rewritten. Requires ``confirm_cost=true`` so nothing is billed without the
    confirmation dialog.
    """
    bind_context(dataset_id=dataset_id)
    if not request.confirm_cost:
        raise ValidationError("confirm_cost must be true; the UI shows the estimate first")
    client = deps.get_comprehend_client()
    if client is None:
        raise ConfigurationError("Comprehend is disabled (COMPREHEND_ENABLED=false)")
    bundle, progress = labeling.label_with_comprehend(
        repo.get(dataset_id), client, settings, request.max_records, deps.get_comprehend_rate()
    )
    if progress.labelled_in_call:
        bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
        repo.save(bundle)
    return progress


@router.put(
    "/dataset/{dataset_id}/labels/review",
    tags=["label"],
    response_model=LabelSummary,
    summary="Choose what to review by hand",
)
def choose_review(dataset_id: str, request: ReviewRequest, repo: RepoDep) -> LabelSummary:
    """Fix the review set.

    ``none`` skips review, ``all`` reviews every record, ``sample`` picks ``size`` records (a
    count or a percent) with a fixed seed so the same request always yields the same subset.
    """
    bundle = labeling.choose_review(
        repo.get(dataset_id), request.mode, request.size, request.unit, request.seed
    )
    repo.save(bundle)
    return labeling.summary(bundle)


@router.get(
    "/dataset/{dataset_id}/labels/review",
    tags=["label"],
    response_model=ReviewPage,
    summary="Page through the review set",
)
def review_page(
    dataset_id: str,
    repo: RepoDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
) -> ReviewPage:
    """Records chosen for review, in review order, with whatever labels they currently carry."""
    total, items = labeling.review_page(repo.get(dataset_id), offset, limit)
    return ReviewPage(total=total, offset=offset, items=items)


@router.post(
    "/dataset/{dataset_id}/labels/manual",
    tags=["label"],
    response_model=LabelSummary,
    summary="Apply reviewer labels",
)
def manual_labels(
    dataset_id: str, request: ManualLabelRequest, repo: RepoDep, checkpoints: CheckpointDep
) -> LabelSummary:
    """Set ``label_source="manual"`` on the given records. Comprehend's label is kept alongside."""
    bind_context(dataset_id=dataset_id)
    bundle = labeling.apply_manual_labels(
        repo.get(dataset_id), [ManualLabel(id=i.id, label=i.label) for i in request.items]
    )
    bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
    repo.save(bundle)
    return labeling.summary(bundle)


# --- checkpoints ------------------------------------------------------------------------------


@router.get(
    "/dataset/{dataset_id}/checkpoints",
    tags=["export"],
    response_model=CheckpointList,
    summary="Stored stage snapshots",
)
def list_checkpoints(dataset_id: str, repo: RepoDep, checkpoints: CheckpointDep) -> CheckpointList:
    """CSV (and any converted Parquet) snapshots for collected, processed and labelled stages."""
    repo.get(dataset_id)  # 404 if unknown
    return CheckpointList(location=deps.checkpoint_location(), items=checkpoints.list(dataset_id))


@router.post(
    "/dataset/{dataset_id}/checkpoints/{stage}/parquet",
    tags=["export"],
    response_model=CheckpointInfo,
    summary="Convert a CSV checkpoint to Parquet",
)
def convert_checkpoint(
    dataset_id: str, stage: Stage, repo: RepoDep, checkpoints: CheckpointDep
) -> CheckpointInfo:
    """Read the stage's CSV snapshot and write a Parquet twin next to it."""
    repo.get(dataset_id)
    csv_bytes = checkpoints.read(dataset_id, stage, "csv")
    if csv_bytes is None:
        raise NotFoundError(f"no {stage} checkpoint for dataset {dataset_id}")
    return checkpoints.save(dataset_id, stage, "parquet", csv_to_parquet(csv_bytes))


# --- export -----------------------------------------------------------------------------------


@router.get(
    "/dataset/{dataset_id}/export.csv",
    tags=["export"],
    summary="Download CSV",
    response_class=Response,
)
def export_csv(dataset_id: str, repo: RepoDep) -> Response:
    """One row per original record: text, processed text, tokens, labels and provenance."""
    return _download(to_csv(repo.get(dataset_id)), "text/csv", f"{dataset_id}.csv")


@router.get(
    "/dataset/{dataset_id}/export.xlsx",
    tags=["export"],
    summary="Download Excel",
    response_class=Response,
)
def export_excel(dataset_id: str, repo: RepoDep) -> Response:
    """Two sheets: ``data`` and ``impact`` (per-step statistics)."""
    return _download(
        to_excel(repo.get(dataset_id)),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"{dataset_id}.xlsx",
    )


@router.get(
    "/dataset/{dataset_id}/export.parquet",
    tags=["export"],
    summary="Download Parquet",
    response_class=Response,
)
def export_parquet(dataset_id: str, repo: RepoDep) -> Response:
    """Same rows as the CSV, typed and compressed. The format Task 2 should load."""
    return _download(
        to_parquet(repo.get(dataset_id)), "application/octet-stream", f"{dataset_id}.parquet"
    )


@router.get(
    "/dataset/{dataset_id}/report.md",
    tags=["export"],
    summary="Task 1 report (Markdown)",
    response_class=Response,
)
def export_report(dataset_id: str, repo: RepoDep) -> Response:
    """Markdown with provenance, measured impact, labelling, and per-step strengths/limitations."""
    return Response(
        content=render_report(repo.get(dataset_id)), media_type="text/markdown; charset=utf-8"
    )


@router.post(
    "/dataset/{dataset_id}/save",
    tags=["export"],
    response_model=SaveResponse,
    summary="Save to S3",
)
def save_dataset(dataset_id: str, repo: RepoDep) -> SaveResponse:
    """Write ``dataset.parquet``, ``impact.json`` and ``manifest.json`` to the data bucket."""
    bind_context(dataset_id=dataset_id)
    return SaveResponse(uri=deps.get_s3_store().save(repo.get(dataset_id)))


# --- account ----------------------------------------------------------------------------------


@router.get("/spend", tags=["account"], response_model=SpendSummary, summary="X spend so far")
def spend(settings: SettingsDep, include_x_usage: bool = Query(False)) -> SpendSummary:
    """Totals from the local ledger (every billed read is recorded) plus the configured caps.

    ``include_x_usage=true`` also asks X for its own usage figure. That call is free but only
    some tiers expose it, so ``x_usage`` is null when unavailable.
    """
    totals = history.spend_totals(datetime.now(UTC).date())
    configured = bool(settings.x_bearer_token or settings.x_bearer_token_ssm_path)
    x_usage = deps.get_x_source().usage() if include_x_usage and configured else None
    return SpendSummary(
        **totals,
        cap_per_fetch=settings.x_max_reads_per_fetch,
        cap_per_day=settings.x_max_reads_per_day,
        remaining_today=max(0, settings.x_max_reads_per_day - totals["today_reads"]),
        cost_per_read_usd=settings.x_cost_per_read_usd,
        x_usage=x_usage,
        x_configured=configured,
    )


@router.get("/history", tags=["account"], response_model=list[HistoryRun], summary="Recent runs")
def recent_history(limit: int = Query(20, ge=1, le=100)) -> list[HistoryRun]:
    """Latest pipeline runs, newest first. Metadata only; record text is never stored here."""
    return [HistoryRun(**row) for row in history.recent_runs(limit)]


# --- helpers ----------------------------------------------------------------------------------


def _store(
    dataset: Dataset, repo: BundleRepository, checkpoints: CheckpointStore, **extra: Any
) -> DatasetSummary:
    bundle = DatasetBundle(dataset_id=uuid.uuid4().hex[:12], original=dataset)
    bind_context(dataset_id=bundle.dataset_id)
    bundle = checkpoint_bundle(checkpoints, bundle, "collected")
    repo.save(bundle)
    history.record_dataset(bundle)
    logger.info("dataset_stored", records=len(dataset.records), source=dataset.source_type)
    return _summary(bundle, **extra)


def _summary(bundle: DatasetBundle, estimated_cost_usd: float | None = None) -> DatasetSummary:
    dataset = bundle.original
    warnings: list[str] = []
    if len(dataset.records) < MIN_RECORDS_FOR_TASK:
        warnings.append(
            f"only {len(dataset.records)} records; Task 1 needs at least {MIN_RECORDS_FOR_TASK}. "
            "Try a broader query or the Hugging Face source."
        )
    return DatasetSummary(
        dataset_id=bundle.dataset_id,
        source_type=dataset.source_type,
        query=dataset.query,
        record_count=len(dataset.records),
        labelled_count=sum(1 for r in dataset.records if r.label),
        truncated_reason=dataset.truncated_reason,
        estimated_cost_usd=estimated_cost_usd,
        warnings=warnings,
        preview=dataset.records[:PREVIEW_ROWS],
    )


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
