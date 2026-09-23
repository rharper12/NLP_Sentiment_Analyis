"""Orchestrates a preprocessing run: pipeline, metrics, and optional AWS enrichments.

Comprehend comparisons are best-effort. A failure records a warning and
leaves the field ``None``; the user still gets the deterministic results.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer
from sentiment_prep.analysis.comprehend_text import TRUNCATION_WARNING, prepare_text
from sentiment_prep.analysis.metrics import DatasetMetrics, compute_metrics
from sentiment_prep.api.schemas import PreprocessRequest
from sentiment_prep.budget import BudgetExhaustedError, can_start
from sentiment_prep.config import Settings
from sentiment_prep.errors import AppError, ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle, ImpactReport
from sentiment_prep.preprocessing import STEP_REGISTRY, Pipeline
from sentiment_prep.preprocessing.base import PreprocessStep
from sentiment_prep.preprocessing.missing_data import MissingDataStep
from sentiment_prep.preprocessing.stopwords import StopwordStep

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient

from sentiment_prep.storage.checkpoints import invalidate_checkpoints

logger = get_logger(__name__)


def build_steps(request: PreprocessRequest) -> list[PreprocessStep]:
    """Instantiate steps in the requested order, applying per-step options."""
    steps: list[PreprocessStep] = []
    for name in request.steps:
        if name not in STEP_REGISTRY:
            raise ValidationError(f"unknown step '{name}'; valid steps: {sorted(STEP_REGISTRY)}")
        if name == MissingDataStep.name:
            steps.append(
                MissingDataStep(
                    strategy=request.options.missing_data_strategy,
                    fill_value=request.options.missing_data_fill_value,
                )
            )
        elif name == StopwordStep.name:
            steps.append(StopwordStep(keep_negations=request.options.keep_negations))
        else:
            steps.append(STEP_REGISTRY[name]())
    return steps


class ProgressPersistenceError(AppError):
    """A required durable write failed; do not continue optional paid work."""


def run_preprocessing(
    bundle: DatasetBundle,
    request: PreprocessRequest,
    settings: Settings,
    comprehend: ComprehendClient | Callable[[], ComprehendClient | None] | None,
    *,
    persist: Callable[[DatasetBundle], None] | None = None,
) -> tuple[DatasetBundle, DatasetMetrics, DatasetMetrics]:
    """Save deterministic output first, then resume cached paid units within this slice.

    A provider may finish before a process dies without committing the result. Such ambiguous
    calls cannot promise exactly-once billing; the exclusive storage claim remains for recovery.
    """
    updated = bundle.model_copy(deep=True)
    state = updated.analysis
    signature = hashlib.sha256(
        (
            request.model_dump_json()
            + json.dumps([(record.id, record.text) for record in updated.original.records])
            + str(settings.comprehend_enabled)
        ).encode()
    ).hexdigest()
    if state.signature != signature or updated.processed is None or updated.report is None:
        processed, step_results = Pipeline(build_steps(request)).run(updated.original)
        invalidate_checkpoints(updated, "processed", "labelled")
        updated.processed = processed
        updated.applied_steps = list(request.steps)
        updated.report = ImpactReport(steps=step_results)
        state.signature = signature
        state.completed = []
    assert updated.processed is not None and updated.report is not None
    processed, report = updated.processed, updated.report
    report.warnings = [w for w in report.warnings if not w.startswith("Analysis partially")]
    state.partial = False

    def save() -> None:
        if persist is not None:
            try:
                persist(updated)
            except Exception as exc:
                # Persistence is mandatory, unlike enrichments. Never swallow this failure
                # in an optional provider handler or start another paid call after it.
                raise ProgressPersistenceError("Could not save analysis progress") from exc

    save()
    for stage in ("sentiment",):
        if stage in state.completed:
            continue
        if not can_start():
            state.partial = True
            break
        attempt_key = signature + ":" + stage
        report.warnings = [w for w in report.warnings if not w.startswith(f"{stage} unavailable")]
        try:
            if stage == "sentiment":
                client = comprehend() if callable(comprehend) else comprehend
                if client is None:
                    report.warnings.append(
                        "sentiment comparison disabled (COMPREHEND_ENABLED=false)"
                    )
                else:
                    if any(
                        prepare_text(r.text).truncated
                        for r in [*updated.original.records, *processed.records]
                    ):
                        report.warnings.append(TRUNCATION_WARNING)
                    scorer = ComprehendScorer(client, cache=state.sentiment, persist=save)
                    report.sentiment = scorer.compare(updated.original, processed)
                    report.warnings = [
                        w
                        for w in report.warnings
                        if w != "Some sentiment comparisons are unavailable"
                    ]
                    if any(r.label == "error" for r in state.sentiment.values()):
                        report.warnings.append("Some sentiment comparisons are unavailable")
                    if scorer.retry_pending:
                        state.partial = True
                        break
        except ProgressPersistenceError:
            raise
        except BudgetExhaustedError:
            state.partial = True
            break
        except Exception as exc:
            # Optional provider failure: keep deterministic output and all committed paid units.
            logger.error("analysis_enrichment_failed", stage=stage, exc_info=True)
            report.warnings.append(f"{stage} unavailable: {type(exc).__name__}")
            state.attempts[attempt_key] = state.attempts.get(attempt_key, 0) + 1
            if state.attempts[attempt_key] < 3:
                state.partial = True
                save()
                break
        state.completed.append(stage)
        save()
    if state.partial:
        report.warnings.append("Analysis partially complete; resume to continue saved work")
    report.warnings = list(dict.fromkeys(report.warnings))
    save()
    return updated, compute_metrics(updated.original), compute_metrics(processed)
