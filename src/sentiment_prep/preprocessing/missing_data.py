"""Drop or fill records whose text is missing, empty, or whitespace."""

from __future__ import annotations

from typing import ClassVar, Literal

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep


class MissingDataStep(PreprocessStep):
    """``drop`` removes bad rows; ``fill`` replaces them with a placeholder token."""

    name: ClassVar[str] = "missing_data"

    def __init__(
        self, strategy: Literal["drop", "fill"] = "drop", fill_value: str = "[EMPTY]"
    ) -> None:
        self._strategy = strategy
        self._fill_value = fill_value

    def transform(self, record: Record) -> Record | None:
        if record.text and record.text.strip():
            return record
        if self._strategy == "drop":
            return None
        return record.model_copy(update={"text": self._fill_value, "tokens": None})
