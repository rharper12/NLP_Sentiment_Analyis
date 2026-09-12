"""Drop or fill records whose text is missing, empty, or whitespace.

Filling keeps the row count stable, which matters when rows are paired with something outside
this dataset. The placeholder becomes a real token in the vocabulary, so it should be a string
that cannot occur naturally in the corpus; the default is bracketed for that reason.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from sentiment_prep.errors import ValidationError
from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep

DEFAULT_FILL_VALUE = "[EMPTY]"
MAX_FILL_VALUE_CHARS = 40


class MissingDataStep(PreprocessStep):
    """``drop`` removes bad rows; ``fill`` replaces them with a placeholder token."""

    name: ClassVar[str] = "missing_data"

    def __init__(
        self,
        strategy: Literal["drop", "fill"] = "drop",
        fill_value: str = DEFAULT_FILL_VALUE,
    ) -> None:
        """Configure how empty text is handled.

        Args:
            strategy: ``drop`` removes the record; ``fill`` substitutes ``fill_value``.
            fill_value: Placeholder written into empty records. Must be non-blank, because a
                blank placeholder would silently leave the record empty and defeat the step.

        Raises:
            ValidationError: if ``fill`` is chosen with a blank or over-long placeholder.
        """
        if strategy == "fill":
            fill_value = fill_value.strip()
            if not fill_value:
                raise ValidationError("fill value cannot be blank; choose a placeholder token")
            if len(fill_value) > MAX_FILL_VALUE_CHARS:
                raise ValidationError(
                    f"fill value must be at most {MAX_FILL_VALUE_CHARS} characters"
                )
        self._strategy = strategy
        self._fill_value = fill_value

    def transform(self, record: Record) -> Record | None:
        if record.text and record.text.strip():
            return record
        if self._strategy == "drop":
            return None
        return record.model_copy(update={"text": self._fill_value, "tokens": None})
