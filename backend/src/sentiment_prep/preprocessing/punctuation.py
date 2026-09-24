"""Remove punctuation, URLs, mentions and other non-word characters.

Hashtags keep their word (``#happy`` becomes ``happy``). Removing mentions, punctuation and
emojis can lose context or sentiment cues; this step is optional.
"""

from __future__ import annotations

import re
from typing import ClassVar

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep

_URL = re.compile(r"https?://\S+|www\.\S+")
_MENTION = re.compile(r"@\w+")
_NON_WORD = re.compile(r"[^\w\s']", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Apply the removals in an order that avoids leaving fragments behind."""
    text = _URL.sub(" ", text)
    text = _MENTION.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    text = text.replace("_", " ")
    return _SPACES.sub(" ", text).strip()


class PunctuationStep(PreprocessStep):
    """Remove non-word characters, links and mentions from text or existing tokens."""

    name: ClassVar[str] = "punctuation"

    def transform(self, record: Record) -> Record | None:
        if record.tokens is not None:
            tokens = [clean_text(t) for t in record.tokens]
            tokens = [t for t in tokens if t]
            return record.model_copy(update={"text": " ".join(tokens), "tokens": tokens})
        return record.model_copy(update={"text": clean_text(record.text)})
