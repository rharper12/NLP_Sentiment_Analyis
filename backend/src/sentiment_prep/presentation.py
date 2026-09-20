"""Public projections shared by JSON and downloadable reports.

Stored domain objects retain diagnostics for operator logs and later diagnostic requests.
"""

from sentiment_prep.analysis.comprehend_text import TRUNCATION_WARNING
from sentiment_prep.models import ImpactReport, ReportContent, StepMetrics


class PublicStepResult(StepMetrics):
    """User metrics with optional operator timing."""

    duration_ms: float | None = None


class PublicImpactReport(ReportContent):
    """A report safe to serialize for the current diagnostics policy."""

    steps: list[PublicStepResult]


def public_report(report: ImpactReport, *, diagnostics: bool = False) -> PublicImpactReport:
    """Project a stored report without modifying its diagnostic facts."""
    data = report.model_dump()
    if not diagnostics:
        for step in data["steps"]:
            step.pop("duration_ms", None)
        warnings = data["warnings"]
        data["warnings"] = [TRUNCATION_WARNING] if TRUNCATION_WARNING in warnings else []
        if any(w != TRUNCATION_WARNING for w in warnings):
            data["warnings"].append("Some optional analysis results are unavailable.")
    return PublicImpactReport.model_validate(data)
