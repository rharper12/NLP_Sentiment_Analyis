"""Reduce words to their dictionary form with the WordNet lemmatizer.

WordNet needs a part-of-speech hint or it treats everything as a noun (turning ``was`` into
``wa``). Tokens are tagged with NLTK's perceptron tagger and the Penn tag is mapped to a
WordNet class. Known limitation: WordNet's verb rules occasionally over-strip (``hated`` to
``hat``); this is recorded in the rationale file rather than patched around.

Corpora required at runtime: ``wordnet``, ``omw-1.4``, ``averaged_perceptron_tagger_eng``
(``make setup`` locally; baked into the Lambda image by the Dockerfile).
"""

from __future__ import annotations

from typing import ClassVar

from nltk import pos_tag
from nltk.corpus import wordnet
from nltk.stem import WordNetLemmatizer

from sentiment_prep.models import Record
from sentiment_prep.preprocessing.base import PreprocessStep

# First letter of a Penn Treebank tag is enough to pick the WordNet class.
_PENN_TO_WORDNET = {"J": wordnet.ADJ, "V": wordnet.VERB, "N": wordnet.NOUN, "R": wordnet.ADV}


class LemmatizeStep(PreprocessStep):
    """POS-aware lemmatization per token."""

    name: ClassVar[str] = "lemmatize"

    def __init__(self) -> None:
        self._lemmatizer = WordNetLemmatizer()

    def transform(self, record: Record) -> Record | None:
        words = record.words()
        if not words:
            return record.model_copy(update={"tokens": [], "text": ""})
        lemmas = [
            str(self._lemmatizer.lemmatize(word, _PENN_TO_WORDNET.get(tag[:1], wordnet.NOUN)))
            for word, tag in pos_tag(words)
        ]
        return record.model_copy(update={"tokens": lemmas, "text": " ".join(lemmas)})
