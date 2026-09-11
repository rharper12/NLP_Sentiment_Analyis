"""One flat row shape shared by CSV, Excel and Parquet so the three exports never disagree."""

from __future__ import annotations

import json
from typing import Any

from sentiment_prep.models import DatasetBundle

ROW_COLUMNS = [
    "id",
    "source_type",
    "label",
    "label_source",
    "label_confidence",
    "comprehend_label",
    "comprehend_confidence",
    "created_at",
    "original_text",
    "processed_text",
    "tokens",
]


def bundle_rows(bundle: DatasetBundle) -> list[dict[str, Any]]:
    """Join original and processed records by id. Dropped records have empty processed fields."""
    processed = {r.id: r for r in bundle.processed.records} if bundle.processed else {}
    rows: list[dict[str, Any]] = []
    for record in bundle.original.records:
        after = processed.get(record.id)
        rows.append(
            {
                "id": record.id,
                "source_type": record.source_type,
                "label": record.label,
                "label_source": record.label_source,
                "label_confidence": record.label_confidence,
                "comprehend_label": record.comprehend_label,
                "comprehend_confidence": record.comprehend_confidence,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "original_text": record.text,
                "processed_text": after.text if after else None,
                "tokens": json.dumps(after.tokens) if after and after.tokens is not None else None,
            }
        )
    return rows
