"""Orchestrates a preprocessing run: pipeline, metrics, and optional AWS enrichments.

Enrichments (Comprehend, embeddings, Bedrock) are best-effort. A failure records a warning and
leaves the field ``None``; the user still gets the deterministic results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentiment_prep.analysis.bedrock_explainer import BedrockExplainer
from sentiment_prep.analysis.comprehend_scorer import ComprehendScorer
from sentiment_prep.analysis.embeddings import EmbeddingDrift
from sentiment_prep.analysis.metrics import DatasetMetrics, compute_metrics
from sentiment_prep.api.schemas import PreprocessRequest
from sentiment_prep.config import Settings
from sentiment_prep.errors import ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle, ImpactReport
from sentiment_prep.preprocessing import STEP_REGISTRY, Pipeline
from sentiment_prep.preprocessing.base import PreprocessStep
from sentiment_prep.preprocessing.missing_data import MissingDataStep
from sentiment_prep.preprocessing.stopwords import StopwordStep

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime.client import BedrockRuntimeClient
    from mypy_boto3_comprehend.client import ComprehendClient

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


def run_preprocessing(
    bundle: DatasetBundle,
    request: PreprocessRequest,
    settings: Settings,
    comprehend: ComprehendClient | None,
    bedrock: BedrockRuntimeClient | None,
) -> tuple[DatasetBundle, DatasetMetrics, DatasetMetrics]:
    """Return the updated bundle plus before/after metrics."""
    processed, step_results = Pipeline(build_steps(request)).run(bundle.original)
    report = ImpactReport(steps=step_results)

    if comprehend is not None:
        try:
            report.sentiment = ComprehendScorer(comprehend).compare(bundle.original, processed)
        # Broad by design: this enrichment is optional and its failure must not cost the caller
        # the deterministic results they already paid for. Logged with a traceback and summarised
        # in `warnings`, so a degraded run is never silent.
        except Exception as exc:
            logger.error("comprehend_comparison_failed", exc_info=True)
            report.warnings.append(f"sentiment comparison unavailable: {type(exc).__name__}")
    else:
        report.warnings.append("sentiment comparison disabled (COMPREHEND_ENABLED=false)")

    if bedrock is not None:
        try:
            report.embedding_drift = EmbeddingDrift(
                bedrock, settings.embed_model_id, settings.embed_sample_size
            ).compute(bundle.original, processed)
        # Optional enrichment: a failure degrades the report rather than failing the request.
        except Exception as exc:
            logger.error("embedding_drift_failed", exc_info=True)
            report.warnings.append(f"embedding drift unavailable: {type(exc).__name__}")
        if request.explain:
            try:
                report.explanation = BedrockExplainer(
                    bedrock, settings.bedrock_text_model_id
                ).explain(report, request.steps)
            # Optional enrichment: a failure degrades the report rather than failing the request.
            except Exception as exc:
                logger.error("explanation_failed", exc_info=True)
                report.warnings.append(f"explanation unavailable: {type(exc).__name__}")
    else:
        report.warnings.append("embedding drift and explanation disabled (BEDROCK_ENABLED=false)")

    updated = bundle.model_copy(
        update={"processed": processed, "applied_steps": list(request.steps), "report": report}
    )
    return updated, compute_metrics(bundle.original), compute_metrics(processed)
