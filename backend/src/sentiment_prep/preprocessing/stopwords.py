"""Remove high-frequency function words.

The class defaults to retaining negations such as ``not`` and ``never`` because removing them
can reverse phrases such as "not good". The API passes its explicit ``keep_negations`` option;
the Clean screen starts with every checkbox unchecked, including that option.
"""

from __future__ import annotations

from typing import ClassVar

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep
from sentiment_prep.preprocessing.stopword_list import ENGLISH_STOPWORDS

NEGATIONS = frozenset({"no", "not", "nor", "never", "n't", "don't", "isn't", "wasn't", "won't"})


class StopwordStep(PreprocessStep):
    """Drop stopwords from the token list (or whitespace words if not yet tokenised)."""

    name: ClassVar[str] = "stopwords"

    def __init__(self, keep_negations: bool = True) -> None:
        self._stopwords = ENGLISH_STOPWORDS - NEGATIONS if keep_negations else ENGLISH_STOPWORDS

    def transform(self, record: Record) -> Record | None:
        kept = [w for w in record.words() if w.lower() not in self._stopwords]
        return record.model_copy(update={"tokens": kept, "text": " ".join(kept)})
