"""Lowercase all text so ``Great`` and ``great`` share one vocabulary entry."""

from __future__ import annotations

from typing import ClassVar

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep


class LowercaseStep(PreprocessStep):
    """Case folding. Loses the emphasis carried by ALL CAPS, which is a real sentiment signal."""

    name: ClassVar[str] = "lowercase"

    def transform(self, record: Record) -> Record | None:
        tokens = [t.lower() for t in record.tokens] if record.tokens is not None else None
        return record.model_copy(update={"text": record.text.lower(), "tokens": tokens})
