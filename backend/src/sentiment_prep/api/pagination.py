"""Bound record pages and analysis responses, including Lambda's outer JSON escaping."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel

from sentiment_prep.errors import ValidationError

if TYPE_CHECKING:
    from sentiment_prep.api.schemas import PreprocessResponse

# Leave 2 MiB of Lambda's 6 MiB response allowance for the envelope and page metadata.
PAGE_BYTES = 4 * 1024 * 1024
ANALYSIS_PREVIEW_WARNING = (
    "Analysis preview details were omitted because of their size. "
    "Dataset and step counts are complete; full text remains available in Records and downloads."
)


def response_size(model: BaseModel) -> int:
    """Estimate a JSON model inside Lambda's envelope, with ASCII-escaped Unicode."""
    return len(json.dumps(model.model_dump_json()))


def bounded_page[T: BaseModel](rows: Iterable[T]) -> list[T]:
    """Return a complete prefix; callers continue at offset + len(items)."""
    items: list[T] = []
    size = 0
    for row in rows:
        # Mangum puts the JSON body inside another JSON string. Count that outer escaping
        # and ASCII-escaped Unicode too, rather than just UTF-8 text or the row count.
        row_size = response_size(row) + 1
        if size + row_size > PAGE_BYTES:
            if not items:
                raise ValidationError("This record is too large to display. Download an export.")
            break
        items.append(row)
        size += row_size
    return items


def bounded_analysis(response: PreprocessResponse) -> PreprocessResponse:
    """Omit optional text previews if the complete analysis response exceeds the budget.

    Metrics and each step's counts remain exact. The records endpoint supplies complete text
    separately; exports use the unchanged stored bundle, including the original sample diffs.
    Normal-sized responses retain every detail.
    """
    if response_size(response) <= PAGE_BYTES:
        return response
    compact = response.model_copy(deep=True)
    compact.preview = []
    compact.metrics_before.top_terms = []
    compact.metrics_after.top_terms = []
    for step in compact.report.steps:
        step.sample_diffs = []
    compact.warnings.append(ANALYSIS_PREVIEW_WARNING)
    if response_size(compact) > PAGE_BYTES:
        raise ValidationError("Analysis summary is too large to display. Download the report.")
    return compact
