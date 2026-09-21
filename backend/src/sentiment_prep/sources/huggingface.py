"""Hugging Face datasets-server adapter.

Uses the public REST ``/rows`` endpoint rather than the ``datasets`` library, which keeps the
Lambda image small and avoids downloading whole parquet shards for a 600-row sample. This is
the free sample path when a live X topic runs dry; availability depends on the public service.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from sentiment_prep.budget import can_start
from sentiment_prep.errors import ExternalServiceError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record
from sentiment_prep.sources.payloads import HFPage

logger = get_logger(__name__)

PAGE_SIZE = 100
MAX_PAGE_ATTEMPTS = 2
PAGE_TIMEOUT = httpx.Timeout(3.0, connect=1.0, pool=1.0)
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
        skipped = 0
        offset = 0
        started = time.perf_counter()
        truncated_reason: str | None = None
        attempts = 0
        last_error: str | None = None

        while len(records) < limit:
            if not can_start():
                truncated_reason = last_error or "request budget reached"
                break
            if should_stop and should_stop():
                truncated_reason = "cancelled by client"
                break
            try:
                response = self._client.get(
                    "/rows",
                    timeout=PAGE_TIMEOUT,
                    params={
                        "dataset": self._dataset,
                        "config": self._config,
                        "split": self._split,
                        "offset": offset,
                        "length": min(PAGE_SIZE, limit - len(records)),
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if isinstance(exc, httpx.TimeoutException):
                    last_error = (
                        "Hugging Face timed out while loading the sample dataset. "
                        "Try loading the sample again, or upload a CSV."
                    )
                elif status is not None:
                    last_error = (
                        f"Hugging Face could not load the sample dataset (HTTP {status}). "
                        "Try again later or upload a CSV."
                    )
                else:
                    last_error = (
                        "Could not reach Hugging Face to load the sample dataset. "
                        "Check your connection and try again, or upload a CSV."
                    )
                attempts += 1
                if attempts < MAX_PAGE_ATTEMPTS and (status is None or status >= 500):
                    logger.warning("hugging_face_page_retry", offset=offset, attempt=attempts)
                    # Retry the same offset, checking cancellation and the shared budget first.
                    continue
                truncated_reason = last_error
                break
            attempts = 0
            last_error = None
            try:
                rows = HFPage.model_validate(response.json()).rows
            except ValueError:
                raise ExternalServiceError("Hugging Face returned malformed dataset rows") from None
            if not rows:
                truncated_reason = "dataset exhausted"
                break
            for item in rows:
                row = item.row
                raw_text = row.get(self._text_column)
                if raw_text is None:
                    skipped += 1
                    continue
                if not isinstance(raw_text, str):
                    raise ExternalServiceError("Hugging Face text must be a string")
                text = raw_text.strip()
                if not text:
                    skipped += 1
                    continue
                records.append(
                    Record(
                        id=f"hf-{item.row_idx}",
                        text=text,
                        label=(label := self._label_for(row)),
                        label_source="source" if label is not None else None,
                        source_type="huggingface",
                    )
                )
            offset += len(rows)

        if not records and last_error is not None:
            # AppError escapes the route's task group through its normal handled-error path.
            raise ExternalServiceError(last_error)
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
            filtered_out={"empty_text": skipped} if skipped else {},
        )

    def _label_for(self, row: dict[str, object]) -> str | None:
        if not self._label_column or self._label_column not in row:
            return None
        raw = row[self._label_column]
        if raw is None:
            return None
        if type(raw) not in (str, int):
            raise ExternalServiceError("Hugging Face label must be a string or integer")
        if isinstance(raw, int) and self._dataset.endswith("tweet_eval"):
            if raw not in TWEET_EVAL_LABELS:
                raise ExternalServiceError("Hugging Face returned an unsupported sentiment label")
            return TWEET_EVAL_LABELS[raw]
        return str(raw)
