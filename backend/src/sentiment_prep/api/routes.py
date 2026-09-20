"""All endpoints, grouped by tag so Swagger reads as the workflow: collect, clean, label, export.

Sync handlers run in FastAPI's threadpool, which is what SQLAlchemy sessions and boto3 expect.
The one async handler, ``load_dataset``, is async so it can notice a client disconnect and stop
a paid X fetch at the next page boundary. Checkpoints are written the moment paid or expensive
data exists: after collect, after preprocessing, and after every labelling call.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any

import anyio
from botocore.exceptions import BotoCoreError
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from sentiment_prep import __version__
from sentiment_prep.api import deps
from sentiment_prep.api.aws_errors import translated
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
from sentiment_prep.api.security import (
    SessionRequest,
    SessionResponse,
    UnauthorizedError,
    create_session,
    require_api_key,
    require_diagnostics,
)
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.api.upload_limit import PayloadTooLargeError
from sentiment_prep.budget import can_start
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import (
    AppError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from sentiment_prep.export.csv_export import to_csv
from sentiment_prep.export.excel_export import to_excel
from sentiment_prep.export.parquet_export import csv_to_parquet, to_parquet
from sentiment_prep.history import db
from sentiment_prep.history import services as history
from sentiment_prep.labeling import service as labeling
from sentiment_prep.labeling.service import LabelEstimate, LabelProgress, ManualLabel
from sentiment_prep.logging_config import bind_context, get_logger
from sentiment_prep.models import CollectionProgress, Dataset, DatasetBundle, LabelSummary
from sentiment_prep.preprocessing import DEFAULT_ORDER
from sentiment_prep.presentation import public_report
from sentiment_prep.report import load_rationale, render_report
from sentiment_prep.sources.base import DataSource
from sentiment_prep.sources.csv_upload import MAX_UPLOAD_BYTES, MAX_UPLOAD_RECORDS, CsvUploadSource
from sentiment_prep.sources.dedupe import deduplicate
from sentiment_prep.storage.checkpoints import (
    CheckpointInfo,
    CheckpointStore,
    Stage,
    checkpoint_bundle,
)
from sentiment_prep.storage.repository import BundleRepository

router = APIRouter()
# Health is deliberately unauthenticated so load balancers and uptime checks can reach it; it
# exposes nothing beyond liveness unless diagnostics are on.
public_router = APIRouter()
logger = get_logger(__name__)

PREVIEW_ROWS = 20
MIN_RECORDS_FOR_TASK = 500
# How often the disconnect watcher polls while a paid fetch runs. Short enough to stop the next
# page promptly, long enough not to spin.
DISCONNECT_POLL_SECONDS = 0.25
# Upper bound on rows accepted from an uploaded CSV, so a stray file cannot exhaust memory.

SettingsDep = Annotated[Settings, Depends(get_settings)]
RepoDep = Annotated[BundleRepository, Depends(deps.get_repository)]
CheckpointDep = Annotated[CheckpointStore, Depends(deps.get_checkpoint_store)]


# --- system -----------------------------------------------------------------------------------


@public_router.post("/auth/session", response_model=SessionResponse, tags=["system"])
def login(body: SessionRequest, settings: SettingsDep, response: Response) -> SessionResponse:
    """Authenticate an operator without distributing the permanent key in public assets."""
    response.headers["Cache-Control"] = "no-store"
    return create_session(body.key.get_secret_value(), settings)


@public_router.get(
    "/health",
    tags=["system"],
    response_model=HealthResponse,
    response_model_exclude_none=True,
    summary="Liveness check",
)
def health(settings: SettingsDep, request: Request) -> HealthResponse:
    """Liveness and, only with diagnostics on, the facts an operator checks first.

    Never touches paid services. Operator-only fields are omitted unless diagnostics are enabled so
    a public deployment reveals nothing about its storage or which paid services are switched on.
    """
    diagnostics = settings.diagnostics_enabled
    if diagnostics:
        try:
            require_api_key(
                settings, request.headers.get("X-API-Key"), request.headers.get("Authorization")
            )
        except UnauthorizedError:
            diagnostics = False
    return HealthResponse(
        status="ok",
        version=__version__,
        diagnostics=diagnostics,
        x_configured=bool(settings.x_bearer_token or settings.x_bearer_token_ssm_path),
        auth_required=bool(
            settings.api_key or settings.api_key_ssm_path or settings.runtime == "lambda"
        ),
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
    source: DataSource | None = deps.get_hf_source() if body.source == "huggingface" else None
    summary: DatasetSummary | None = None
    dataset: Dataset | None = None
    stop = threading.Event()
    failure: AppError | None = None

    async def watch_disconnect() -> None:
        while not stop.is_set():
            if await request.is_disconnected():
                logger.warning("client_disconnected_during_fetch")
                stop.set()
                return
            await anyio.sleep(DISCONNECT_POLL_SECONDS)

    async with anyio.create_task_group() as group:
        group.start_soon(watch_disconnect)
        try:
            if body.source == "x":
                summary = await anyio.to_thread.run_sync(
                    lambda: _collect_x(body, repo, checkpoints, settings, stop.is_set)
                )
            else:
                assert source is not None
                dataset = await anyio.to_thread.run_sync(
                    lambda: source.fetch(
                        limit=body.limit, query=body.query, should_stop=stop.is_set
                    )
                )
        except AppError as exc:
            failure = exc
        finally:
            stop.set()

    if failure is not None:
        raise failure
    if summary is not None:
        return summary
    assert dataset is not None
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
    limit: int = Query(MAX_UPLOAD_RECORDS, ge=1, le=MAX_UPLOAD_RECORDS),
) -> DatasetSummary:
    """CSV with a ``text`` column and optional ``label``/``id`` columns."""
    content = bytearray()
    while chunk := await file.read(min(65536, MAX_UPLOAD_BYTES + 1 - len(content))):
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError("CSV exceeds the 4 MiB upload limit")
    bind_context(source="csv", filename=file.filename, bytes=len(content))
    return await anyio.to_thread.run_sync(
        lambda: _store(CsvUploadSource(bytes(content)).fetch(limit=limit), repo, checkpoints)
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
) -> PreprocessResponse | Response:
    """Apply steps in order. Always re-runs from the original data, so toggles are idempotent.

    Comprehend, Titan embeddings and Bedrock are best-effort: if any is disabled or fails the
    corresponding report field is null and ``report.warnings`` says why. The ``processed``
    checkpoint is rewritten on every run.
    """
    bind_context(dataset_id=dataset_id, steps=request.steps)
    with repo.edit(dataset_id) as edit:
        bundle = edit.bundle
        updated, before, after = run_preprocessing(
            bundle,
            request,
            settings,
            deps.get_comprehend_client,
            deps.get_bedrock_client,
            persist=edit.save,
        )
        if can_start():
            updated = checkpoint_bundle(checkpoints, updated, "processed")
            edit.save(updated)
        if not updated.analysis.partial and can_start():
            history.record_run(updated)
    if updated.processed is None or updated.report is None:  # pragma: no cover - invariant
        raise AppError("preprocessing produced no result")
    response = PreprocessResponse(
        dataset_id=dataset_id,
        applied_steps=updated.applied_steps,
        partial=updated.analysis.partial,
        record_count=len(updated.processed.records),
        metrics_before=before,
        metrics_after=after,
        report=public_report(updated.report, diagnostics=settings.diagnostics_enabled),
        preview=updated.processed.records[:PREVIEW_ROWS],
    )
    if settings.diagnostics_enabled:
        return response
    return JSONResponse(
        content=response.model_dump(
            mode="json",
            exclude={
                "report": {"steps": {"__all__": {"duration_ms"}}},
            },
        )
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

    Resumable: successful labels and permanent failures are skipped; temporary failures have
    at most three attempts. Each call saves successes and failure counts before writing a
    labelled checkpoint. Requires ``confirm_cost=true`` so nothing is billed without the
    confirmation dialog.
    """
    bind_context(dataset_id=dataset_id)
    if not request.confirm_cost:
        raise ValidationError("confirm_cost must be true; the UI shows the estimate first")
    with repo.edit(dataset_id) as edit:
        with translated("Amazon Comprehend"):
            try:
                client = deps.get_comprehend_client()
            except (BotoCoreError, AppError):
                raise
            except Exception:  # noqa: BLE001 - isolate required client initialization only
                raise ConfigurationError(
                    "Cannot initialize Amazon Comprehend. Check AWS configuration and credentials."
                ) from None
            if client is None:
                raise ConfigurationError("Comprehend is disabled (COMPREHEND_ENABLED=false)")
            bundle, progress = labeling.label_with_comprehend(
                edit.bundle,
                client,
                settings,
                request.max_records,
                deps.get_comprehend_rate(),
                persist=edit.save,
            )
        if progress.labelled_in_call and can_start():
            bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
            edit.save(bundle)
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
    with repo.edit(dataset_id) as edit:
        bundle = labeling.choose_review(
            edit.bundle, request.mode, request.size, request.unit, request.seed
        )
        edit.save(bundle)
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
    with repo.edit(dataset_id) as edit:
        bundle = labeling.apply_manual_labels(
            edit.bundle, [ManualLabel(id=i.id, label=i.label) for i in request.items]
        )
        bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
        edit.save(bundle)
    return labeling.summary(bundle)


# --- checkpoints ------------------------------------------------------------------------------


@router.get(
    "/dataset/{dataset_id}/checkpoints",
    tags=["export"],
    response_model=CheckpointList,
    dependencies=[Depends(require_diagnostics)],
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
    dependencies=[Depends(require_diagnostics)],
    summary="Convert a CSV checkpoint to Parquet",
)
def convert_checkpoint(
    dataset_id: str, stage: Stage, repo: RepoDep, checkpoints: CheckpointDep
) -> CheckpointInfo:
    """Read the stage's CSV snapshot and write a Parquet twin next to it."""
    with repo.edit(dataset_id):
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
def export_excel(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> Response:
    """Two sheets: ``data`` and ``impact`` (per-step statistics)."""
    return _download(
        to_excel(repo.get(dataset_id), diagnostics=settings.diagnostics_enabled),
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
def export_report(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> Response:
    """Markdown with provenance, measured impact, labelling, and per-step strengths/limitations."""
    return _download(
        render_report(repo.get(dataset_id), diagnostics=settings.diagnostics_enabled).encode(),
        "text/markdown; charset=utf-8",
        f"{dataset_id}-report.md",
    )


@router.post(
    "/dataset/{dataset_id}/save",
    tags=["export"],
    response_model=SaveResponse,
    response_model_exclude_none=True,
    summary="Save to S3",
)
def save_dataset(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> SaveResponse:
    """Write ``dataset.parquet``, ``impact.json`` and ``manifest.json`` to the data bucket."""
    bind_context(dataset_id=dataset_id)
    uri = deps.get_s3_store().save(repo.get(dataset_id), diagnostics=settings.diagnostics_enabled)
    return SaveResponse(uri=uri if settings.diagnostics_enabled else None)


# --- account ----------------------------------------------------------------------------------


@router.get("/spend", tags=["account"], response_model=SpendSummary, summary="X spend so far")
def spend(settings: SettingsDep, include_x_usage: bool = Query(False)) -> SpendSummary:
    """Totals from the local ledger (every billed read is recorded) plus the configured caps.

    ``include_x_usage=true`` also asks X for its own usage figure. That call is free but only
    some tiers expose it, so ``x_usage`` is null when unavailable.
    """
    today = datetime.now(UTC).date()
    totals = history.spend_totals(today)
    # Remaining budget counts reservations, not just billed reads, so a fetch in flight elsewhere
    # is already subtracted here.
    committed = deps.get_spend_ledger().reserved(today.isoformat())
    configured = bool(settings.x_bearer_token or settings.x_bearer_token_ssm_path)
    x_usage = (
        deps.get_x_source().usage()
        if include_x_usage and configured and settings.diagnostics_enabled
        else None
    )
    return SpendSummary(
        **totals,
        cap_per_fetch=settings.x_max_reads_per_fetch,
        cap_per_day=settings.x_max_reads_per_day,
        remaining_today=max(0, settings.x_max_reads_per_day - committed),
        cost_per_read_usd=settings.x_cost_per_read_usd,
        x_usage=x_usage,
        x_configured=configured,
    )


@router.get(
    "/history",
    tags=["account"],
    response_model=list[HistoryRun],
    summary="Recent runs",
)
def recent_history(
    settings: SettingsDep, limit: int = Query(20, ge=1, le=100)
) -> list[HistoryRun] | Response:
    """Latest pipeline runs, newest first. Metadata only; record text is never stored here."""
    rows = history.recent_runs(limit)
    if not settings.diagnostics_enabled:
        for row in rows:
            row.pop("duration_ms", None)
    records = [HistoryRun(**row) for row in rows]
    if settings.diagnostics_enabled:
        return records
    return JSONResponse(content=[record.model_dump(exclude={"duration_ms"}) for record in records])


# --- helpers ----------------------------------------------------------------------------------


def _collect_x(
    body: LoadRequest,
    repo: BundleRepository,
    checkpoints: CheckpointStore,
    settings: Settings,
    should_stop: Callable[[], bool] | None = None,
) -> DatasetSummary:
    """Stable request identity plus the existing exclusive edit, across all page slices."""
    # Validate durable spend storage/token before creating a job. No paid work occurs here.
    source = deps.get_x_source(body.query or "", body.start_time, body.end_time)
    request_id = body.request_id or uuid.uuid4().hex
    dataset_id = "x-" + request_id
    identity = body.model_dump(mode="json", exclude={"request_id"})
    try:
        repo.get(dataset_id)
    except NotFoundError:
        bundle = DatasetBundle(
            dataset_id=dataset_id,
            original=Dataset(
                records=[],
                source_type="x",
                query=body.query,
                window_start=body.start_time,
                window_end=body.end_time,
            ),
            collection=CollectionProgress(request=identity),
        )
        with suppress(ConflictError):  # Another creator may win; edit arbitrates ownership.
            repo.save(bundle)
    with repo.edit(dataset_id) as edit:
        bundle = edit.bundle
        if bundle.collection is None or bundle.collection.request != identity:
            raise ValidationError("request_id already belongs to a different collection request")
        if not bundle.collection.complete:

            def persist(dataset: Dataset, progress: CollectionProgress) -> None:
                nonlocal bundle
                bundle = bundle.model_copy(
                    update={"original": dataset, "collection": progress.model_copy(deep=True)}
                )
                edit.save(bundle)

            source.fetch(
                body.limit,
                body.query,
                should_stop=should_stop,
                resume=bundle.original,
                progress=bundle.collection,
                persist=persist,
            )
        if bundle.collection and bundle.collection.complete and settings.dedupe_enabled:
            kept, removed = deduplicate(bundle.original.records, settings.dedupe_similarity)
            if removed:
                bundle = bundle.model_copy(
                    update={
                        "original": bundle.original.model_copy(
                            update={
                                "records": kept,
                                "filtered_out": {**bundle.original.filtered_out, **removed},
                            }
                        )
                    }
                )
                edit.save(bundle)
        if can_start():
            bundle = checkpoint_bundle(checkpoints, bundle, "collected")
            edit.save(bundle)
            history.record_dataset(bundle)
    return _summary(
        bundle,
        estimated_cost_usd=round(bundle.collection.reads * settings.x_cost_per_read_usd, 4)
        if bundle.collection
        else None,
    )


def _store(
    dataset: Dataset, repo: BundleRepository, checkpoints: CheckpointStore, **extra: Any
) -> DatasetSummary:
    """Deduplicate, persist, checkpoint and summarise a freshly collected dataset.

    Deduplication happens here rather than in each adapter so every source gets it, and before
    the pipeline so the record count is fixed for every preprocessing configuration that follows.
    """
    settings = get_settings()
    if settings.dedupe_enabled:
        kept, removed = deduplicate(dataset.records, settings.dedupe_similarity)
        if removed:
            dataset = dataset.model_copy(
                update={
                    "records": kept,
                    "filtered_out": {**dataset.filtered_out, **removed},
                }
            )
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
        window_start=dataset.window_start,
        window_end=dataset.window_end,
        record_count=len(dataset.records),
        labelled_count=sum(1 for r in dataset.records if r.label),
        filtered_out=dataset.filtered_out,
        truncated_reason=dataset.truncated_reason,
        estimated_cost_usd=estimated_cost_usd,
        partial=bundle.collection is not None and not bundle.collection.complete,
        resume_request_id=bundle.dataset_id.removeprefix("x-") if bundle.collection else None,
        retry_at=bundle.collection.retry_at or None if bundle.collection else None,
        warnings=warnings,
        preview=dataset.records[:PREVIEW_ROWS],
    )


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
