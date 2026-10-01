"""Sentiment labels from Amazon Comprehend, with confidence.

Two callers share this module: the Analyze stage scores records before and after preprocessing
to measure agreement, and the Label stage stores per-record labels for Task 2. Results are cached
by text hash and persisted by the caller after each batch. Committed successes are reused;
a provider response lost before commit remains an unavoidable billing ambiguity.

Comprehend returns POSITIVE, NEGATIVE, NEUTRAL or MIXED plus a score per class; the label is
lower-cased and the confidence is the score of the winning class.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal
from itertools import batched
from typing import TYPE_CHECKING, Literal, NamedTuple

from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient
    from mypy_boto3_comprehend.literals import LanguageCodeType

from sentiment_prep.analysis.comprehend_text import prepare_text
from sentiment_prep.analysis.payloads import SentimentResponse
from sentiment_prep.aws import is_sso_session_error
from sentiment_prep.budget import require_budget
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import SENTIMENT_LABELS, CachedSentiment, Dataset, SentimentComparison

logger = get_logger(__name__)

CENT_PRECISION = Decimal("0.0001")  # Comprehend prices are quoted to four decimal places
BATCH_SIZE = 25  # Comprehend's hard limit per BatchDetectSentiment call


class SentimentLabel(NamedTuple):
    """Winning class and its score. ``label`` is ``"error"`` when Comprehend rejected the text."""

    label: str
    confidence: float | None
    error_code: Literal["document_rejected", "temporarily_unavailable", "missing_result"] | None = (
        None
    )
    retryable: bool = False


def billable_units(text: str, unit_chars: int, min_units: int) -> int:
    """Units Comprehend bills for one document: ceil(chars / unit_chars), at least ``min_units``."""
    chars = prepare_text(text).submitted_chars
    return max(min_units, -(-chars // unit_chars))


def count_units(texts: Iterable[str], unit_chars: int, min_units: int) -> int:
    """Total units for labeling batches, including the scorer's within-batch text reuse.

    Args:
        texts: Documents that would be sent to Comprehend.
        unit_chars: Characters per billable unit (100 for sentiment).
        min_units: Minimum units charged per document (3 for sentiment).

    Returns:
        The number of units Comprehend would bill. No price is applied; see ``cost_for_units``.
    """
    return sum(
        billable_units(text, unit_chars, min_units)
        for batch in batched(texts, BATCH_SIZE)
        for text in dict.fromkeys(prepare_text(text).text for text in batch)
    )


def cost_for_units(units: int, price_per_unit: Decimal) -> Decimal:
    """Cost of ``units`` at ``price_per_unit``.

    The single place money is multiplied. ``Decimal`` throughout and quantised once, so repeated
    slices cannot accumulate binary-float error; callers convert to ``float`` only at the API edge.

    Args:
        units: Billable units, from ``count_units``.
        price_per_unit: Published price, parsed from the Price List API without passing via float.

    Returns:
        Cost in USD, quantised to the four decimal places AWS publishes.
    """
    return (Decimal(units) * price_per_unit).quantize(CENT_PRECISION, rounding=ROUND_HALF_UP)


class ComprehendScorer:
    """Label records; compare two labelled datasets."""

    def __init__(
        self,
        client: ComprehendClient,
        language_code: LanguageCodeType = "en",
        *,
        cache: dict[str, CachedSentiment] | None = None,
        persist: Callable[[], None] | None = None,
    ) -> None:
        """Score documents with Amazon Comprehend.

        Args:
        client: boto3 Comprehend client, shared and owned by the caller.
        cache: Durable text results loaded from the bundle.
        persist: Commit the cache after each batch.
        language_code: Language passed to Comprehend; documents in other languages are
            rejected by the service rather than mislabelled.
        """
        self._client = client
        self._language = language_code
        self._cache: dict[str, SentimentLabel] = {}
        self._stored = cache if cache is not None else {}
        self._persist = persist
        self._used: set[str] = set()
        for key, result in self._stored.items():
            if not result.retryable or result.attempts >= 3 or result.label != "error":
                self._cache[key] = SentimentLabel(
                    result.label, result.confidence, result.error_code, result.retryable
                )

    def label(self, dataset: Dataset) -> list[SentimentLabel]:
        """Return one ``SentimentLabel`` per record, in order."""
        return self.label_texts([r.text for r in dataset.records])

    def label_texts(self, texts: list[str]) -> list[SentimentLabel]:
        """Label raw strings, calling Comprehend only for texts not already cached."""
        prepared = [prepare_text(t).text for t in texts]
        self._used.update(self._key(t) for t in prepared)
        missing = [t for t in dict.fromkeys(prepared) if self._key(t) not in self._cache]
        for start in range(0, len(missing), BATCH_SIZE):
            require_budget()
            batch = missing[start : start + BATCH_SIZE]
            self._score_batch(batch)
            for text in batch:
                key = self._key(text)
                result = self._cache.get(key, SentimentLabel("error", None, "missing_result", True))
                self._cache[key] = result
                previous = self._stored.get(key)
                self._stored[key] = CachedSentiment(
                    label=result.label,
                    confidence=result.confidence,
                    error_code=result.error_code,
                    retryable=result.retryable,
                    attempts=previous.attempts + 1 if previous else 1,
                )
            if self._persist is not None:
                self._persist()
        return [
            self._cache.get(self._key(t), SentimentLabel("error", None, "missing_result", True))
            for t in prepared
        ]

    @property
    def retry_pending(self) -> bool:
        """Only failures for inputs in this comparison may keep it resumable."""
        return any(
            result.label == "error" and result.retryable and result.attempts < 3
            for key, result in self._stored.items()
            if key in self._used
        )

    def compare(self, before: Dataset, after: Dataset) -> SentimentComparison:
        """Fraction of records whose label survived preprocessing, plus both distributions.

        Records dropped by preprocessing are matched by id so a ``missing_data`` step does not
        misalign the comparison.
        """
        labels_before = dict(
            zip((r.id for r in before.records), (x.label for x in self.label(before)), strict=True)
        )
        labels_after = dict(
            zip((r.id for r in after.records), (x.label for x in self.label(after)), strict=True)
        )
        shared = [rid for rid in labels_after if rid in labels_before]
        comparable = [
            rid
            for rid in shared
            if labels_before[rid] in SENTIMENT_LABELS and labels_after[rid] in SENTIMENT_LABELS
        ]
        agreed = sum(1 for rid in comparable if labels_before[rid] == labels_after[rid])
        comparison = SentimentComparison(
            agreement=round(agreed / len(comparable), 4) if comparable else None,
            comparable_records=len(comparable),
            shared_records=len(shared),
            distribution_before=dict(
                Counter(x for x in labels_before.values() if x in SENTIMENT_LABELS)
            ),
            distribution_after=dict(
                Counter(x for x in labels_after.values() if x in SENTIMENT_LABELS)
            ),
        )
        logger.info("sentiment_comparison", **comparison.model_dump())
        return comparison

    def _score_batch(self, texts: list[str]) -> None:
        try:
            response = self._client.batch_detect_sentiment(
                TextList=texts, LanguageCode=self._language
            )
        except (ClientError, BotoCoreError) as error:
            # Authentication blocks the whole request, not individual documents. Let the caller
            # pause without exhausting their scoring attempts or submitting further batches.
            if is_sso_session_error(error):
                raise
            # Preserve earlier successful batches. These are service/transport failures, not
            # evidence that a document is invalid; the caller persists a bounded retry count.
            logger.error("comprehend_batch_failed", exc_info=True)
            for text in texts:
                self._cache[self._key(text)] = SentimentLabel(
                    "error", None, "temporarily_unavailable", True
                )
            return
        try:
            validated = SentimentResponse.model_validate(response)
            validated.check_indices(len(texts))
        except ValueError:
            logger.warning("comprehend_invalid_response")
            for text in texts:
                self._cache[self._key(text)] = SentimentLabel(
                    "error", None, "temporarily_unavailable", True
                )
            return
        for result in validated.results:
            sentiment = result.sentiment.lower()
            confidence = round(result.scores.model_dump()[sentiment], 4)
            self._cache[self._key(texts[result.index])] = SentimentLabel(sentiment, confidence)
        for failure in validated.errors:
            permanent = failure.code in {
                "TEXT_SIZE_LIMIT_EXCEEDED",
                "UNSUPPORTED_LANGUAGE",
                "INVALID_REQUEST",
                "INVALID_TEXT",
            }
            self._cache[self._key(texts[failure.index])] = SentimentLabel(
                "error",
                None,
                "document_rejected" if permanent else "temporarily_unavailable",
                not permanent,
            )
        logger.info("comprehend_batch_scored", documents=len(texts))

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()
