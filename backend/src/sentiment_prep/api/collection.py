"""Collection claims, persistence and summaries shared by the dataset HTTP endpoints.

Keep cursor, billing and source records in the same exclusive edit. HTTP handlers own
request cancellation; this module owns durable collection state and checkpoint ordering.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, tzinfo
from decimal import Decimal

from sentiment_prep import eligibility
from sentiment_prep.api import deps
from sentiment_prep.api.schemas import AdditionalCandidatesRequest, DatasetSummary, LoadRequest
from sentiment_prep.budget import can_start
from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import ConflictError, NotFoundError, ValidationError
from sentiment_prep.filenames import bundle_file_stem, default_file_stem
from sentiment_prep.history import services as history
from sentiment_prep.logging_config import bind_context, get_logger
from sentiment_prep.models import CollectionProgress, ConsumerPolicy, Dataset, DatasetBundle
from sentiment_prep.sources.dedupe import deduplicate
from sentiment_prep.storage.checkpoints import (
    CheckpointStore,
    checkpoint_bundle,
    checkpoint_warnings,
    invalidate_checkpoints,
)
from sentiment_prep.storage.repository import BundleRepository

logger = get_logger(__name__)
PREVIEW_ROWS = 20
MIN_RECORDS_FOR_TASK = 500


def extend_candidate_target(
    dataset_id: str,
    body: AdditionalCandidatesRequest,
    repo: BundleRepository,
    settings: Settings,
) -> LoadRequest:
    """Authorize a larger quota without changing the saved search or paying for a fetch."""
    if not body.confirm_cost:
        raise ValidationError("Confirm the cost before collecting additional candidates")
    with repo.edit(dataset_id) as edit:
        bundle = edit.bundle
        progress = bundle.collection
        if not bundle.original.consumer_policy or progress is None:
            raise ValidationError("Only consumer collections support additional candidates")
        if eligibility.counts(bundle).shortfall == 0:
            raise ValidationError(
                "The reviewed target has been reached; no more candidates are needed"
            )
        if bundle.original.consumer_policy.duplicate_threshold != settings.dedupe_similarity:
            raise ValidationError("Screening settings changed; start a new collection")
        if progress.terminal_error:
            raise ValidationError(
                "Correct the provider access/request error and start a new collection"
            )
        previous = progress.candidate_target or int(progress.request.get("limit") or 0)
        if body.candidate_target < previous:
            raise ValidationError("The additional candidate target cannot decrease")
        if body.candidate_target > previous:
            progress.candidate_target = body.candidate_target
            progress.complete = False
            edit.save(bundle)
        return LoadRequest.model_validate(
            {**progress.request, "request_id": dataset_id.removeprefix("x-")}
        )


def collect_x(
    body: LoadRequest,
    repo: BundleRepository,
    checkpoints: CheckpointStore,
    settings: Settings,
    should_stop: Callable[[], bool] | None = None,
    *,
    zone: tzinfo = UTC,
) -> DatasetSummary:
    """Stable request identity plus the existing exclusive edit, across all page slices."""
    # Validate durable spend storage/token before creating a job. No paid work occurs here.
    source = deps.get_x_source(body.query or "", body.start_time, body.end_time)
    request_id = body.request_id or uuid.uuid4().hex
    dataset_id = "x-" + request_id
    identity = body.model_dump(mode="json", exclude={"request_id"})
    try:
        existing = repo.get(dataset_id)
    except NotFoundError:
        existing = None
    policy = (
        ConsumerPolicy(
            version=existing.original.consumer_policy.version
            if existing and existing.original.consumer_policy
            else "consumer-reactions-v2",
            start_date=body.start_date,
            end_date=body.end_date,
            timezone=body.timezone,
            per_author_limit=body.per_author_limit,
            reviewed_target=body.reviewed_target,
            duplicate_threshold=settings.dedupe_similarity,
        )
        if body.preset == "consumer_reactions" and body.start_date and body.end_date
        else None
    )
    if existing is None:
        bundle = DatasetBundle(
            dataset_id=dataset_id,
            file_stem=default_file_stem(body.query, "x", zone),
            original=Dataset(
                records=[],
                source_type="x",
                query=body.query,
                window_start=body.start_time,
                window_end=body.end_time,
                consumer_policy=policy,
            ),
            collection=CollectionProgress(
                request=identity, billed_reads=0, committed_cost_usd=Decimal("0")
            ),
        )
        with suppress(ConflictError):  # Another creator may win; edit arbitrates ownership.
            repo.save(bundle)
    with repo.edit(dataset_id) as edit:
        bundle = edit.bundle
        # Old general jobs predate optional preset fields; compare validated defaults too.
        saved_identity = (
            LoadRequest.model_validate(bundle.collection.request).model_dump(
                mode="json", exclude={"request_id"}
            )
            if bundle.collection
            else None
        )
        if saved_identity != identity or bundle.original.consumer_policy != policy:
            raise ValidationError("request_id already belongs to a different collection request")
        assert bundle.collection is not None
        # Recheck under the collection claim: review may finish after the quota was extended.
        if bundle.collection.needs_more_records(len(bundle.original.records)) and (
            not policy or eligibility.counts(bundle).shortfall > 0
        ):
            saved_before = len(bundle.original.records)
            first_batch = (
                bundle.collection.first_batch_saved is None
                and bundle.collection.billed_reads == 0
                and saved_before == 0
            )

            def persist(dataset: Dataset, progress: CollectionProgress) -> None:
                nonlocal bundle
                # Commit the request delta with each page, including interrupted requests.
                progress.last_batch_saved = max(0, len(dataset.records) - saved_before)
                if first_batch:
                    progress.first_batch_saved = progress.last_batch_saved
                # Cursor/accounting/stop-reason updates alone do not change source rows.
                if dataset.records != bundle.original.records:
                    invalidate_checkpoints(bundle, "collected")
                    if policy:
                        eligibility.invalidate_derived(bundle)
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
                dedupe_similarity=settings.dedupe_similarity if settings.dedupe_enabled else None,
                **({"consumer_policy": policy} if policy else {}),
            )
        if can_start():
            bundle = checkpoint_bundle(checkpoints, bundle, "collected")
            edit.save(bundle)
            history.record_dataset(bundle)
    return summarize(bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled)


def store_dataset(
    dataset: Dataset,
    repo: BundleRepository,
    checkpoints: CheckpointStore,
    *,
    dedupe: bool = True,
    zone: tzinfo = UTC,
) -> DatasetSummary:
    """Deduplicate, persist, checkpoint and summarise a freshly collected dataset.

    CSV and sample imports share this boundary. X deduplicates inside its paging loop so it can
    keep fetching until the retained-record target is met. Restoring original data skips dedupe.
    """
    settings = get_settings()
    if dedupe and settings.dedupe_enabled:
        kept, removed = deduplicate(dataset.records, settings.dedupe_similarity)
        if removed:
            dataset = dataset.model_copy(
                update={
                    "records": kept,
                    "filtered_out": {**dataset.filtered_out, **removed},
                }
            )
    bundle = DatasetBundle(
        dataset_id=uuid.uuid4().hex[:12],
        original=dataset,
        file_stem=default_file_stem(dataset.query, dataset.source_type, zone),
    )
    bind_context(dataset_id=bundle.dataset_id)
    bundle = checkpoint_bundle(checkpoints, bundle, "collected")
    repo.save(bundle)
    history.record_dataset(bundle)
    logger.info("dataset_stored", records=len(dataset.records), source=dataset.source_type)
    return summarize(bundle, local_dev=settings.runtime == "local" and settings.diagnostics_enabled)


def summarize(bundle: DatasetBundle, *, local_dev: bool = False) -> DatasetSummary:
    """Derive response counts and resumability from the current persisted bundle."""
    dataset = bundle.original
    partial = bundle.collection is not None and bundle.collection.needs_more_records(
        len(dataset.records)
    )
    warnings: list[str] = checkpoint_warnings(bundle, local_dev=local_dev)
    if len(dataset.records) < MIN_RECORDS_FOR_TASK and not dataset.consumer_policy:
        warnings.append(
            f"only {len(dataset.records)} records; Task 1 needs at least {MIN_RECORDS_FOR_TASK}. "
            + (
                "Resume collection to continue from saved progress."
                if partial
                else "Try a broader query or the Hugging Face source."
            )
        )
    return DatasetSummary(
        dataset_id=bundle.dataset_id,
        file_stem=bundle_file_stem(bundle),
        source_type=dataset.source_type,
        query=dataset.query,
        window_start=dataset.window_start,
        window_end=dataset.window_end,
        record_count=len(dataset.records),
        labelled_count=sum(1 for r in dataset.records if r.label),
        filtered_out=dataset.filtered_out,
        truncated_reason=dataset.truncated_reason
        or ("more unique posts needed; resume collection" if partial else None),
        billed_reads=bundle.collection.billed_reads if bundle.collection else None,
        committed_cost_usd=float(bundle.collection.committed_cost_usd)
        if bundle.collection and bundle.collection.committed_cost_usd is not None
        else None,
        first_batch_saved=bundle.collection.first_batch_saved if bundle.collection else None,
        last_batch_saved=bundle.collection.last_batch_saved if bundle.collection else None,
        partial=partial,
        resume_request_id=bundle.dataset_id.removeprefix("x-") if bundle.collection else None,
        retry_at=bundle.collection.retry_at or None if bundle.collection else None,
        warnings=warnings,
        preview=dataset.records[:PREVIEW_ROWS],
        consumer_policy=dataset.consumer_policy,
        consumer_counts=eligibility.counts(bundle) if dataset.consumer_policy else None,
        candidate_target=(
            bundle.collection.candidate_target or int(bundle.collection.request.get("limit") or 0)
        )
        if bundle.collection
        else None,
        can_collect_more=bool(
            dataset.consumer_policy
            and bundle.collection
            and not bundle.collection.terminal_error
            and any(not block.exhausted for block in bundle.collection.slices)
        ),
    )
