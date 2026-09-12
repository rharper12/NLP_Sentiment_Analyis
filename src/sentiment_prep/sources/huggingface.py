"""Hugging Face datasets-server adapter.

Uses the public REST ``/rows`` endpoint rather than the ``datasets`` library, which keeps the
Lambda image small and avoids downloading whole parquet shards for a 600-row sample. This is
the guaranteed path to the 500-record minimum when a live X topic runs dry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record

logger = get_logger(__name__)

PAGE_SIZE = 100
# tweet_eval/sentiment encodes labels as integers; keep the mapping explicit for the report.
TWEET_EVAL_LABELS = {0: "negative", 1: "neutral", 2: "positive"}


class HuggingFaceSource:
    """Fetch labelled rows from a public dataset."""

    name = "huggingface"

    def __init__(
        self,
        dataset: str,
        config: str,
        split: str,
        text_column: str,
        label_column: str | None,
        client: httpx.Client,
    ) -> None:
        """Read rows from a public Hugging Face dataset.

        Args:
        dataset: Hub dataset id, e.g. ``cardiffnlp/tweet_eval``.
        config: Dataset configuration name.
        split: Split to read, e.g. ``train``.
        text_column: Column holding the text.
        label_column: Column holding the label, or ``None`` for an unlabelled dataset.
        client: Shared ``httpx.Client``; owned by the caller and never closed here.
        """
        self._dataset = dataset
        self._config = config
        self._split = split
        self._text_column = text_column
        self._label_column = label_column
        # Shared, pooled client owned by the caller; this class never closes it.
        self._client = client

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Dataset:
        """Page through ``/rows`` until ``limit`` records. ``query`` is ignored."""
        records: list[Record] = []
        offset = 0
        started = time.perf_counter()
        truncated_reason: str | None = None

        while len(records) < limit:
            if should_stop and should_stop():
                truncated_reason = "cancelled by client"
                break
            response = self._client.get(
                "/rows",
                params={
                    "dataset": self._dataset,
                    "config": self._config,
                    "split": self._split,
                    "offset": offset,
                    "length": min(PAGE_SIZE, limit - len(records)),
                },
            )
            response.raise_for_status()
            rows = response.json().get("rows", [])
            if not rows:
                truncated_reason = "dataset exhausted"
                break
            for item in rows:
                row = item["row"]
                text = str(row.get(self._text_column, "")).strip()
                if not text:
                    continue
                records.append(
                    Record(
                        id=f"hf-{item['row_idx']}",
                        text=text,
                        label=(label := self._label_for(row)),
                        label_source="source" if label is not None else None,
                        source_type="huggingface",
                    )
                )
            offset += len(rows)

        logger.info(
            "hugging_face_fetch_complete",
            dataset=self._dataset,
            requested=limit,
            returned=len(records),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return Dataset(
            records=records[:limit],
            source_type="huggingface",
            query=f"{self._dataset}/{self._config}/{self._split}",
            truncated_reason=truncated_reason,
        )

    def _label_for(self, row: dict[str, Any]) -> str | None:
        if not self._label_column or self._label_column not in row:
            return None
        raw = row[self._label_column]
        if isinstance(raw, int) and self._dataset.endswith("tweet_eval"):
            return TWEET_EVAL_LABELS.get(raw, str(raw))
        return str(raw)
