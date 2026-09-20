"""Embedding drift: how far preprocessing moved records in Titan Embed v2 space.

A deterministic sample keeps cost fixed and results reproducible across toggles. Mean cosine
distance near 0 means the embedding model saw the two versions as nearly the same text;
values above ~0.3 mean the preprocessing materially changed meaning as the model sees it.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable
from typing import TYPE_CHECKING

from sentiment_prep.analysis.payloads import EMBEDDING_DIMENSIONS, TitanResponse
from sentiment_prep.budget import require_budget
from sentiment_prep.errors import ExternalServiceError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime.client import BedrockRuntimeClient

logger = get_logger(__name__)


class EmbeddingDrift:
    """Compute mean cosine distance between before/after texts for a fixed sample."""

    def __init__(
        self,
        client: BedrockRuntimeClient,
        model_id: str,
        sample_size: int = 50,
        seed: int = 7,
        *,
        cache: dict[str, list[float]] | None = None,
        attempts: dict[str, int] | None = None,
        persist: Callable[[], None] | None = None,
    ) -> None:
        """Measure how far preprocessing moved records in embedding space.

        Args:
        client: boto3 Bedrock runtime client, shared and owned by the caller.
        model_id: Embedding model id.
        sample_size: Records embedded per run; caps the cost of a drift measurement.
        seed: Fixes the sample so repeated runs on one dataset are comparable.
        cache: Durable vectors keyed by model and input text.
        attempts: Durable retry counts.
        persist: Commit progress before another paid invocation.
        """
        self._client = client
        self._model_id = model_id
        self._sample_size = sample_size
        self._seed = seed
        self._cache = cache if cache is not None else {}
        self._attempts = attempts if attempts is not None else {}
        self._persist = persist

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
        key = hashlib.sha256((self._model_id + "\0" + text).encode()).hexdigest()
        if key in self._cache:
            return TitanResponse.model_validate({"embedding": self._cache[key]}).embedding
        if self._attempts.get(key, 0) >= 3:
            raise ExternalServiceError("Embedding retry limit reached")
        require_budget()
        self._attempts[key] = self._attempts.get(key, 0) + 1
        try:
            vector = self._invoke(text)
        except Exception:
            # Retain bounded attempts for optional provider/transport failures.
            if self._persist is not None:
                self._persist()
            raise
        self._cache[key] = vector
        if self._persist is not None:
            self._persist()
        return vector

    def _invoke(self, text: str) -> list[float]:
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(
                {"inputText": text or " ", "dimensions": EMBEDDING_DIMENSIONS, "normalize": True}
            ),
            contentType="application/json",
            accept="application/json",
        )
        with response["body"] as body:
            return TitanResponse.model_validate_json(body.read()).embedding


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0 when either vector is all zeros."""
    # Rescaling before products prevents finite, very large values overflowing to NaN.
    scale_a, scale_b = max(map(abs, a), default=0), max(map(abs, b), default=0)
    if not scale_a or not scale_b:
        return 0.0
    a, b = [x / scale_a for x in a], [x / scale_b for x in b]
    dot = math.fsum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(math.fsum(x * x for x in a)) * math.sqrt(math.fsum(y * y for y in b))
    return max(-1.0, min(1.0, dot / norm))
