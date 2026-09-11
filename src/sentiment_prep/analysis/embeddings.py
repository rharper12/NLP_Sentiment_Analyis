"""Embedding drift: how far preprocessing moved records in Titan Embed v2 space.

A deterministic sample keeps cost fixed and results reproducible across toggles. Mean cosine
distance near 0 means the embedding model saw the two versions as nearly the same text;
values above ~0.3 mean the preprocessing materially changed meaning as the model sees it.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset

logger = get_logger(__name__)


class EmbeddingDrift:
    """Compute mean cosine distance between before/after texts for a fixed sample."""

    def __init__(self, client: Any, model_id: str, sample_size: int = 50, seed: int = 7) -> None:
        self._client = client
        self._model_id = model_id
        self._sample_size = sample_size
        self._seed = seed

    def compute(self, before: Dataset, after: Dataset) -> float:
        """Sample ids present in both datasets, embed both versions, average the distance."""
        after_by_id = {r.id: r for r in after.records}
        shared = [r for r in before.records if r.id in after_by_id]
        if not shared:
            return 0.0
        rng = random.Random(self._seed)
        sample = rng.sample(shared, min(self._sample_size, len(shared)))
        distances = [self._distance(r.text, after_by_id[r.id].text) for r in sample]
        drift = round(sum(distances) / len(distances), 4)
        logger.info("embedding_drift", sample=len(sample), mean_cosine_distance=drift)
        return drift

    def _distance(self, before: str, after: str) -> float:
        """Identical strings are distance 0 by definition; skipping the call also saves money.

        Without this, two empty texts embed to zero vectors and read as maximally different.
        """
        if before.strip() == after.strip():
            return 0.0
        return 1.0 - cosine(self._embed(before), self._embed(after))

    def _embed(self, text: str) -> list[float]:
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=json.dumps({"inputText": text or " ", "dimensions": 1024, "normalize": True}),
            contentType="application/json",
            accept="application/json",
        )
        vector: list[float] = json.loads(response["body"].read())["embedding"]
        return vector


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0 when either vector is all zeros."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
