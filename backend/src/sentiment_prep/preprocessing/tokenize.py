"""Split text into tokens with NLTK's Treebank tokenizer.

Treebank needs no downloaded model, unlike ``word_tokenize``/punkt, which matters in Lambda.
After this step ``Record.tokens`` is set and ``text`` becomes the space-joined tokens so
downstream steps and exports see one consistent representation.
"""

from __future__ import annotations

from typing import ClassVar

from nltk.tokenize import TreebankWordTokenizer

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep


class TokenizeStep(PreprocessStep):
    """Word-level tokenisation."""

    name: ClassVar[str] = "tokenize"

    def __init__(self) -> None:
        self._tokenizer = TreebankWordTokenizer()

    def transform(self, record: Record) -> Record | None:
        tokens = self._tokenizer.tokenize(record.text)
        return record.model_copy(update={"tokens": tokens, "text": " ".join(tokens)})
