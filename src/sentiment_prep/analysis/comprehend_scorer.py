"""Sentiment labels from Amazon Comprehend, with confidence.

Two callers share this module: the Analyze stage scores records before and after preprocessing
to measure agreement, and the Label stage stores per-record labels for Task 2. Results are cached
by text hash within a scorer instance because toggling a step usually leaves most records
unchanged, and because every billed unit should be paid for once.

Comprehend returns POSITIVE, NEGATIVE, NEUTRAL or MIXED plus a score per class; the label is
lower-cased and the confidence is the score of the winning class.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient
    from mypy_boto3_comprehend.literals import LanguageCodeType

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, SentimentComparison

logger = get_logger(__name__)

CENT_PRECISION = Decimal("0.0001")  # Comprehend prices are quoted to four decimal places
BATCH_SIZE = 25  # Comprehend's hard limit per BatchDetectSentiment call
MAX_BYTES = 5000  # per-document limit; longer texts are truncated, not rejected


class SentimentLabel(NamedTuple):
    """Winning class and its score. ``label`` is ``"error"`` when Comprehend rejected the text."""

    label: str
    confidence: float | None


def billable_units(text: str, unit_chars: int, min_units: int) -> int:
    """Units Comprehend bills for one document: ceil(chars / unit_chars), at least ``min_units``."""
    chars = len(text.encode("utf-8")[:MAX_BYTES])
    return max(min_units, -(-chars // unit_chars))


def count_units(texts: Iterable[str], unit_chars: int, min_units: int) -> int:
    """Total billable units for a set of documents.

    Args:
        texts: Documents that would be sent to Comprehend.
        unit_chars: Characters per billable unit (100 for sentiment).
        min_units: Minimum units charged per document (3 for sentiment).

    Returns:
        The number of units Comprehend would bill. No price is applied; see ``cost_for_units``.
    """
    return sum(billable_units(t, unit_chars, min_units) for t in texts)


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

    def __init__(self, client: ComprehendClient, language_code: LanguageCodeType = "en") -> None:
        """Score documents with Amazon Comprehend.

        Args:
        client: boto3 Comprehend client, shared and owned by the caller.
        language_code: Language passed to Comprehend; documents in other languages are
            rejected by the service rather than mislabelled.
        """
        self._client = client
        self._language = language_code
        self._cache: dict[str, SentimentLabel] = {}

    def label(self, dataset: Dataset) -> list[SentimentLabel]:
        """Return one ``SentimentLabel`` per record, in order."""
        return self.label_texts([r.text for r in dataset.records])

    def label_texts(self, texts: list[str]) -> list[SentimentLabel]:
        """Label raw strings, calling Comprehend only for texts not already cached."""
        prepared = [(t or " ")[:MAX_BYTES] for t in texts]
        missing = [t for t in dict.fromkeys(prepared) if self._key(t) not in self._cache]
        for start in range(0, len(missing), BATCH_SIZE):
            self._score_batch(missing[start : start + BATCH_SIZE])
        return [self._cache.get(self._key(t), SentimentLabel("error", None)) for t in prepared]

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
        agreed = sum(1 for rid in shared if labels_before[rid] == labels_after[rid])
        comparison = SentimentComparison(
            agreement=round(agreed / len(shared), 4) if shared else 0.0,
            distribution_before=dict(Counter(labels_before.values())),
            distribution_after=dict(Counter(labels_after.values())),
        )
        logger.info("sentiment_comparison", **comparison.model_dump())
        return comparison

    def _score_batch(self, texts: list[str]) -> None:
        response = self._client.batch_detect_sentiment(TextList=texts, LanguageCode=self._language)
        for result in response.get("ResultList", []):
            sentiment = result["Sentiment"]
            # SentimentScore is keyed by the capitalised class name, e.g. {"Positive": 0.98}.
            # The stub types it as a heterogeneous dict, so narrow before rounding.
            score = result.get("SentimentScore", {}).get(sentiment.capitalize())
            confidence = round(score, 4) if isinstance(score, float) else None
            self._cache[self._key(texts[result["Index"]])] = SentimentLabel(
                sentiment.lower(), confidence
            )
        for failure in response.get("ErrorList", []):
            self._cache[self._key(texts[failure["Index"]])] = SentimentLabel("error", None)
            logger.warning("comprehend_rejected_document", code=failure.get("ErrorCode"))
        logger.info("comprehend_batch_scored", documents=len(texts))

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()
