"""All endpoints, grouped by tag so Swagger reads as the workflow: collect, clean, label, export.

Sync handlers run in FastAPI's threadpool. Async collection watches for disconnects; async CSV
handlers read uploads before moving parsing and persistence to a worker thread. Stage checkpoints
follow collection, preprocessing and labelling; working bundles also save paid batch progress.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import anyio
from botocore.exceptions import BotoCoreError
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError as ModelValidationError

from sentiment_prep import __version__, eligibility
from sentiment_prep.api import deps
from sentiment_prep.api.aws_errors import translated
from sentiment_prep.api.collection import (
    PREVIEW_ROWS,
    collect_x,
    extend_candidate_target,
    store_dataset,
    summarize,
)
from sentiment_prep.api.schemas import (
    AdditionalCandidatesRequest,
    CheckpointList,
    ComprehendLabelRequest,
    CsvValidation,
    DatasetSummary,
    EligibilityPage,
    EligibilityRequest,
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
    require_local_datasets,
)
from sentiment_prep.api.service import run_preprocessing
from sentiment_prep.api.upload_limit import read_csv_upload
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
from sentiment_prep.export.parquet_export import csv_to_parquet, to_parquet, to_reviewed_parquet
from sentiment_prep.filenames import FileStem, bundle_file_stem
from sentiment_prep.history import db
from sentiment_prep.history import services as history
from sentiment_prep.labeling import service as labeling
from sentiment_prep.labeling.service import LabelEstimate, LabelProgress, ManualLabel
from sentiment_prep.logging_config import bind_context, get_logger
from sentiment_prep.models import (
    CheckpointState,
    Dataset,
    LabelSummary,
    Record,
)
from sentiment_prep.preprocessing import DEFAULT_ORDER, STEP_GROUPS
from sentiment_prep.presentation import public_report
from sentiment_prep.report import load_rationale, render_report
from sentiment_prep.sources.base import DataSource
from sentiment_prep.sources.csv_upload import MAX_UPLOAD_RECORDS, CsvUploadSource
from sentiment_prep.sources.saved_dataset import export_original, original_only
from sentiment_prep.storage.checkpoints import (
    CheckpointInfo,
    CheckpointStore,
    Stage,
    checkpoint_bundle,
    checkpoint_info,
    checkpoint_warnings,
)
from sentiment_prep.storage.local_repository import LocalDatasetPage, LocalRepository
from sentiment_prep.storage.repository import BundleRepository

router = APIRouter()
# Health is deliberately unauthenticated so load balancers and uptime checks can reach it; it
# exposes nothing beyond liveness unless diagnostics are on.
public_router = APIRouter()
logger = get_logger(__name__)

# How often the disconnect watcher polls while a paid fetch runs. Short enough to stop the next
# page promptly, long enough not to spin.
DISCONNECT_POLL_SECONDS = 0.25

SettingsDep = Annotated[Settings, Depends(get_settings)]
RepoDep = Annotated[BundleRepository, Depends(deps.get_repository)]
CheckpointDep = Annotated[CheckpointStore, Depends(deps.get_checkpoint_store)]
TimeZoneDep = Annotated[ZoneInfo, Depends(deps.browser_time_zone)]
FilenameQuery = Annotated[
    FileStem | None, Query(description="Filename without its fixed extension")
]


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
        local_datasets_available=settings.local_datasets_available,
        version=__version__,
        diagnostics=diagnostics,
        x_cost_per_read_usd=settings.x_cost_per_read_usd,
        x_configured=bool(settings.x_bearer_token or settings.x_bearer_token_ssm_path),
        auth_required=bool(
            settings.api_key or settings.api_key_ssm_path or settings.runtime == "lambda"
        ),
        runtime=settings.runtime if diagnostics else None,
        database=db.backend_name() if diagnostics else None,
        database_ephemeral=db.is_ephemeral() if diagnostics else None,
        checkpoints=deps.checkpoint_location() if diagnostics else None,
        comprehend_enabled=settings.comprehend_enabled if diagnostics else None,
    )


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
    zone: TimeZoneDep,
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
                    lambda: collect_x(body, repo, checkpoints, settings, stop.is_set, zone=zone)
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
    # Database and checkpoint writes are synchronous; keep them off the event loop.
    return await anyio.to_thread.run_sync(
        lambda: store_dataset(dataset, repo, checkpoints, zone=zone)
    )


@router.post("/dataset/{dataset_id}/candidates", tags=["dataset"], response_model=DatasetSummary)
async def additional_candidates(
    dataset_id: str,
    body: AdditionalCandidatesRequest,
    request: Request,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
    zone: TimeZoneDep,
) -> DatasetSummary:
    """Explicitly extend a candidate quota; reuse approved criteria, cursors and cumulative caps."""
    approved = await anyio.to_thread.run_sync(
        lambda: extend_candidate_target(dataset_id, body, repo, settings)
    )
    return await load_dataset(approved, request, repo, checkpoints, settings, zone)


@router.post(
    "/dataset/upload", tags=["dataset"], response_model=DatasetSummary, summary="Upload a CSV"
)
async def upload_dataset(
    repo: RepoDep,
    checkpoints: CheckpointDep,
    file: Annotated[UploadFile, File()],
    zone: TimeZoneDep,
    limit: int = Query(MAX_UPLOAD_RECORDS, ge=1, le=MAX_UPLOAD_RECORDS),
) -> DatasetSummary:
    """CSV with a ``text`` column and optional ``label``/``id`` columns."""
    content = await read_csv_upload(file)
    bind_context(source="csv", filename=file.filename, bytes=len(content))
    return await anyio.to_thread.run_sync(
        lambda: store_dataset(
            CsvUploadSource(content).fetch(limit=limit), repo, checkpoints, zone=zone
        )
    )


@router.post(
    "/dataset/upload/validate",
    tags=["dataset"],
    response_model=CsvValidation,
    summary="Validate a CSV without creating a dataset",
)
async def validate_csv(file: Annotated[UploadFile, File()]) -> CsvValidation:
    """Use the import parser for a read-only preview; import validates the file again."""
    content = await read_csv_upload(file)
    dataset = await anyio.to_thread.run_sync(
        lambda: CsvUploadSource(content).fetch(limit=MAX_UPLOAD_RECORDS)
    )
    return CsvValidation(
        record_count=len(dataset.records),
        skipped_empty=dataset.filtered_out.get("empty_text", 0),
        labelled_count=sum(record.label is not None for record in dataset.records),
        preview=dataset.records[:3],
    )


@router.get(
    "/local-datasets",
    tags=["dataset"],
    response_model=LocalDatasetPage,
    dependencies=[Depends(require_local_datasets)],
    summary="List local saved JSON datasets",
)
def local_datasets(
    settings: SettingsDep,
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> LocalDatasetPage:
    """Page through local working files by modification time without loading their contents."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return LocalRepository(Path(settings.checkpoint_dir) / "_work").list_files(offset, limit)
    except OSError as exc:
        logger.warning("local_dataset_listing_failed", error_type=type(exc).__name__)
        raise ValidationError(
            "Could not read saved datasets. Check the local folder permissions."
        ) from exc


@router.post(
    "/local-datasets/{dataset_id}/restore",
    tags=["dataset"],
    response_model=DatasetSummary,
    dependencies=[Depends(require_local_datasets)],
    summary="Open a local original dataset in Clean",
)
def restore_local_dataset(
    dataset_id: str,
    settings: SettingsDep,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    zone: TimeZoneDep,
) -> DatasetSummary:
    """Reopen consumer review intact; general datasets start a fresh original-only run."""
    try:
        saved = LocalRepository(Path(settings.checkpoint_dir) / "_work").get(dataset_id)
    except (ModelValidationError, OSError) as exc:
        logger.warning(
            "local_dataset_restore_failed", dataset_id=dataset_id, error_type=type(exc).__name__
        )
        raise ValidationError(
            "Could not open this saved dataset. It must be a valid local dataset JSON file."
        ) from exc
    minimum = 0 if saved.original.consumer_policy else 1
    # X's minimum page size may retain nine extra candidates in each requested day.
    maximum = MAX_UPLOAD_RECORDS
    if saved.original.consumer_policy:
        policy = saved.original.consumer_policy
        maximum += 9 * ((policy.end_date - policy.start_date).days + 1)
    if not minimum <= len(saved.original.records) <= maximum:
        raise ValidationError("Saved dataset must contain between 1 and 5,000 original records")
    if saved.original.consumer_policy:
        # Consumer review is an ongoing collection, not a new original-only cleaning exercise.
        try:
            return summarize(repo.get(dataset_id), local_dev=settings.diagnostics_enabled)
        except NotFoundError:
            repo.save(saved)
            return summarize(saved, local_dev=settings.diagnostics_enabled)
    return store_dataset(original_only(saved.original), repo, checkpoints, dedupe=False, zone=zone)


@router.get(
    "/dataset/{dataset_id}",
    tags=["dataset"],
    response_model=DatasetSummary,
    summary="Dataset summary",
)
def get_dataset(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> DatasetSummary:
    """Summary and preview of a previously loaded dataset."""
    return summarize(
        repo.get(dataset_id), local_dev=settings.runtime == "local" and settings.diagnostics_enabled
    )


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


@router.get(
    "/steps", tags=["preprocess"], response_model=list[StepInfo], summary="List available steps"
)
def list_steps() -> list[StepInfo]:
    """Catalogue of steps with their human-written rationale, in the recommended order."""
    rationale = load_rationale()
    return [
        StepInfo(name=name, group=STEP_GROUPS[name], **rationale[name]) for name in DEFAULT_ORDER
    ]


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

    Comprehend comparisons are best-effort: if the service is disabled or fails the
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
            persist=edit.save,
        )
        if can_start():
            updated = checkpoint_bundle(checkpoints, updated, "processed")
            edit.save(updated)
        if (
            updated.original.consumer_policy
            and "labelled:csv" in updated.checkpoint_status
            and can_start()
        ):
            # Consumer labels precede cleaning. Refresh their snapshot with the new text
            # without asking the reviewer to repeat or change saved human decisions.
            updated = checkpoint_bundle(checkpoints, updated, "labelled")
            edit.save(updated)
        if not updated.analysis.partial and can_start():
            history.record_run(updated)
    if updated.processed is None or updated.report is None:  # pragma: no cover - invariant
        raise AppError("preprocessing produced no result")
    response = PreprocessResponse(
        dataset_id=dataset_id,
        applied_steps=updated.applied_steps,
        partial=updated.analysis.partial,
        warnings=checkpoint_warnings(
            updated, local_dev=settings.runtime == "local" and settings.diagnostics_enabled
        ),
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


@router.get("/dataset/{dataset_id}/eligibility", tags=["label"], response_model=EligibilityPage)
def eligibility_page(
    dataset_id: str,
    repo: RepoDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
    status: Literal["all", "pending", "include", "exclude", "sentiment", "needs_review"] = "all",
    start_at: Literal["first_unreviewed", "first_unlabeled"] | None = None,
) -> EligibilityPage:
    """Inspect original candidates and recoverable exclusions without enrichment calls."""
    bundle = repo.get(dataset_id)
    selected = eligibility.included_ids(bundle)
    rows = bundle.original.records

    def needs_review(record: Record, sentiment: bool) -> bool:
        return (
            not record.eligibility_reviewed
            or record.eligibility == "pending"
            or (sentiment and record.id in selected and not record.sentiment_reviewed)
        )

    if start_at is not None:
        if status != "all":
            raise ValidationError("Starting at an unfinished post requires the all-posts view")
        # Seek once, then navigate stable original offsets so Previous can revisit saved posts.
        offset = next(
            (i for i, r in enumerate(rows) if needs_review(r, start_at == "first_unlabeled")),
            len(rows),
        )
    if status == "needs_review":
        rows = [r for r in rows if needs_review(r, True)]
    elif status == "sentiment":
        rows = [r for r in rows if r.id in selected]
    elif status == "pending":
        rows = [r for r in rows if needs_review(r, False)]
    elif status != "all":
        rows = [r for r in rows if r.eligibility_reviewed and r.eligibility == status]
    return EligibilityPage(
        total=len(rows),
        offset=offset,
        items=rows[offset : offset + limit],
        included_ids=sorted(selected),
        counts=eligibility.counts(bundle),
    )


@router.put("/dataset/{dataset_id}/eligibility", tags=["label"], response_model=DatasetSummary)
def review_eligibility(
    dataset_id: str,
    body: EligibilityRequest,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
) -> DatasetSummary:
    """Apply explicit human decisions, including unchanged confirmations and corrections."""
    with repo.edit(dataset_id) as edit:
        bundle = eligibility.apply_decisions(edit.bundle, body.items)
        edit.save(bundle)
        bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
        edit.save(bundle)
    return summarize(bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled)


@router.get(
    "/dataset/{dataset_id}/labels/summary",
    tags=["label"],
    response_model=LabelSummary,
    summary="Label coverage and agreement",
)
def label_summary(dataset_id: str, repo: RepoDep, settings: SettingsDep) -> LabelSummary:
    """Counts by source and label, review progress, reviewer-vs-Comprehend agreement."""
    return labeling.summary(
        repo.get(dataset_id), local_dev=settings.runtime == "local" and settings.diagnostics_enabled
    )


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
        with translated(
            "Amazon Comprehend",
            local_dev=settings.runtime == "local" and settings.diagnostics_enabled,
        ):
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
    progress.warnings = checkpoint_warnings(
        bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled
    )
    return progress


@router.put(
    "/dataset/{dataset_id}/labels/review",
    tags=["label"],
    response_model=LabelSummary,
    summary="Choose what to review by hand",
)
def choose_review(
    dataset_id: str, request: ReviewRequest, repo: RepoDep, settings: SettingsDep
) -> LabelSummary:
    """Fix the review set.

    ``none`` skips review, ``all`` reviews every record, and ``sample`` uses a fixed seed.
    ``low_confidence`` prioritizes unreviewed records with missing or low Comprehend scores.
    """
    with repo.edit(dataset_id) as edit:
        bundle = labeling.choose_review(
            edit.bundle, request.mode, request.size, request.unit, request.seed
        )
        edit.save(bundle)
    return labeling.summary(
        bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled
    )


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
    bundle = repo.get(dataset_id)
    total, items = labeling.review_page(bundle, offset, limit)
    return ReviewPage(
        total=total, offset=offset, items=items, reviewed=labeling.summary(bundle).reviewed
    )


@router.post(
    "/dataset/{dataset_id}/labels/manual",
    tags=["label"],
    response_model=LabelSummary,
    summary="Apply reviewer labels",
)
def manual_labels(
    dataset_id: str,
    request: ManualLabelRequest,
    repo: RepoDep,
    checkpoints: CheckpointDep,
    settings: SettingsDep,
) -> LabelSummary:
    """Set ``label_source="manual"`` on the given records. Comprehend's label is kept alongside."""
    bind_context(dataset_id=dataset_id)
    with repo.edit(dataset_id) as edit:
        bundle = labeling.apply_manual_labels(
            edit.bundle, [ManualLabel(id=i.id, label=i.label) for i in request.items]
        )
        bundle = checkpoint_bundle(checkpoints, bundle, "labelled")
        edit.save(bundle)
    return labeling.summary(
        bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled
    )


@router.get(
    "/dataset/{dataset_id}/checkpoints",
    tags=["export"],
    response_model=CheckpointList,
    dependencies=[Depends(require_diagnostics)],
    summary="Stored stage snapshots",
)
def list_checkpoints(dataset_id: str, repo: RepoDep, checkpoints: CheckpointDep) -> CheckpointList:
    """CSV (and any converted Parquet) snapshots for collected, processed and labelled stages."""
    bundle = repo.get(dataset_id)
    return CheckpointList(
        location=deps.checkpoint_location(),
        items=[checkpoint_info(info, bundle) for info in checkpoints.list(dataset_id)],
    )


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
    with repo.edit(dataset_id) as edit:
        bundle = edit.bundle
        source = bundle.checkpoint_status.get(f"{stage}:csv")
        if source and source.status != "current":
            raise ConflictError("Regenerate the outdated CSV checkpoint before converting it.")
        csv_bytes = checkpoints.read(dataset_id, stage, "csv")
        if csv_bytes is None:
            raise NotFoundError(f"no {stage} checkpoint for dataset {dataset_id}")
        key = f"{stage}:parquet"
        try:
            info = checkpoints.save(dataset_id, stage, "parquet", csv_to_parquet(csv_bytes))
        except Exception as exc:
            previous = bundle.checkpoint_status.get(key, CheckpointState())
            bundle.checkpoint_status[key] = previous.model_copy(update={"status": "failed"})
            edit.save(bundle)
            raise AppError(
                "Checkpoint conversion failed. Retry; any previous snapshot has been retained."
            ) from exc
        bundle.checkpoint_status[key] = CheckpointState(
            revision=hashlib.sha256(csv_bytes).hexdigest(),
            status="current" if source else "stale",
        )
        edit.save(bundle)
        return checkpoint_info(info, bundle)


@router.get(
    "/dataset/{dataset_id}/original.json",
    tags=["export"],
    summary="Download original data for a new cleaning run",
)
def download_original(dataset_id: str, repo: RepoDep) -> Response:
    """Portable original records/provenance, with no processing or paid-work state."""
    bundle = repo.get(dataset_id)
    return _download(
        export_original(bundle.original),
        "application/json",
        f"{bundle_file_stem(bundle)}.json",
    )


@router.get(
    "/dataset/{dataset_id}/export.csv",
    tags=["export"],
    summary="Download CSV",
    response_class=Response,
)
def export_csv(dataset_id: str, repo: RepoDep, filename: FilenameQuery = None) -> Response:
    """One row per original record: text, processed text, tokens, labels and provenance."""
    bundle = repo.get(dataset_id)
    return _download(to_csv(bundle), "text/csv", f"{filename or bundle_file_stem(bundle)}.csv")


@router.get(
    "/dataset/{dataset_id}/export.xlsx",
    tags=["export"],
    summary="Download Excel",
    response_class=Response,
)
def export_excel(
    dataset_id: str, repo: RepoDep, settings: SettingsDep, filename: FilenameQuery = None
) -> Response:
    """Two sheets: ``data`` and ``impact`` (per-step statistics)."""
    bundle = repo.get(dataset_id)
    return _download(
        to_excel(bundle, diagnostics=settings.diagnostics_enabled),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"{filename or bundle_file_stem(bundle)}.xlsx",
    )


@router.get(
    "/dataset/{dataset_id}/export.parquet",
    tags=["export"],
    summary="Download Parquet",
    response_class=Response,
)
def export_parquet(dataset_id: str, repo: RepoDep, filename: FilenameQuery = None) -> Response:
    """Same rows as the CSV, typed and compressed. The format Task 2 should load."""
    bundle = repo.get(dataset_id)
    return _download(
        to_parquet(bundle),
        "application/octet-stream",
        f"{filename or bundle_file_stem(bundle)}.parquet",
    )


@router.get("/dataset/{dataset_id}/reviewed.parquet", tags=["export"], response_class=Response)
def export_reviewed(dataset_id: str, repo: RepoDep, filename: FilenameQuery = None) -> Response:
    """Fresh reviewed consumer rows plus embedded policy and completion metadata."""
    bundle = repo.get(dataset_id)
    return _download(
        to_reviewed_parquet(bundle),
        "application/octet-stream",
        f"{filename or (bundle_file_stem(bundle)[:111] + '-reviewed')}.parquet",
    )


@router.get(
    "/dataset/{dataset_id}/report.md",
    tags=["export"],
    summary="Task 1 report (Markdown)",
    response_class=Response,
)
def export_report(
    dataset_id: str, repo: RepoDep, settings: SettingsDep, filename: FilenameQuery = None
) -> Response:
    """Markdown with provenance, measured impact, labelling, and per-step strengths/limitations."""
    bundle = repo.get(dataset_id)
    return _download(
        render_report(bundle, diagnostics=settings.diagnostics_enabled).encode(),
        "text/markdown; charset=utf-8",
        f"{filename or bundle_file_stem(bundle)}.md",
    )


@router.post(
    "/dataset/{dataset_id}/save",
    tags=["export"],
    response_model=SaveResponse,
    response_model_exclude_none=True,
    summary="Save to S3",
)
def save_dataset(
    dataset_id: str, repo: RepoDep, settings: SettingsDep, filename: FilenameQuery = None
) -> SaveResponse:
    """Write named Parquet data, impact and manifest into a new S3 save folder."""
    bind_context(dataset_id=dataset_id)
    uri = deps.get_s3_store().save(
        repo.get(dataset_id), diagnostics=settings.diagnostics_enabled, filename=filename
    )
    return SaveResponse(uri=uri if settings.diagnostics_enabled else None)


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


def _download(content: bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
