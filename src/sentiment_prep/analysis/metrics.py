"""Pure dataset statistics. No I/O, no AWS, fully unit-testable."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from sentiment_prep.models import Dataset

LENGTH_BUCKETS = ((0, 5), (6, 10), (11, 20), (21, 40), (41, 10**6))


class DatasetMetrics(BaseModel):
    """Shape of a dataset in numbers a reader can compare before/after."""

    record_count: int
    vocab_size: int
    total_tokens: int
    avg_tokens: float
    type_token_ratio: float
    length_histogram: dict[str, int]
    top_terms: list[tuple[str, int]]


def compute_metrics(dataset: Dataset, top_n: int = 15) -> DatasetMetrics:
    """Summarise vocabulary, length and frequent terms."""
    counter: Counter[str] = Counter()
    lengths: list[int] = []
    for record in dataset.records:
        words = record.words()
        counter.update(words)
        lengths.append(len(words))

    total = sum(lengths)
    histogram = {f"{lo}-{hi if hi < 10**6 else '+'}": 0 for lo, hi in LENGTH_BUCKETS}
    for n in lengths:
        for (lo, hi), key in zip(LENGTH_BUCKETS, histogram, strict=True):
            if lo <= n <= hi:
                histogram[key] += 1
                break

    return DatasetMetrics(
        record_count=len(lengths),
        vocab_size=len(counter),
        total_tokens=total,
        avg_tokens=round(total / len(lengths), 2) if lengths else 0.0,
        type_token_ratio=round(len(counter) / total, 4) if total else 0.0,
        length_histogram=histogram,
        top_terms=counter.most_common(top_n),
    )
