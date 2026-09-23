"""Import original data without restoring jobs, credentials, analysis, or paid-work state."""

from __future__ import annotations

import json

from sentiment_prep.models import Dataset

FORMAT = "sentiment-prep-original-v1"


def original_only(dataset: Dataset) -> Dataset:
    """Keep original text/provenance and source labels; discard later annotations/tokens."""
    return dataset.model_copy(
        update={
            "records": [
                record.model_copy(
                    update={
                        "tokens": None,
                        "label": record.label if record.label_source == "source" else None,
                        "label_source": "source" if record.label_source == "source" else None,
                        "label_confidence": None,
                        "comprehend_label": None,
                        "comprehend_confidence": None,
                    }
                )
                for record in dataset.records
            ]
        }
    )


def export_original(dataset: Dataset) -> bytes:
    """Serialize portable original data without annotations or application state."""
    return json.dumps(
        {"format": FORMAT, "original": original_only(dataset).model_dump(mode="json")},
        ensure_ascii=False,
    ).encode("utf-8")
