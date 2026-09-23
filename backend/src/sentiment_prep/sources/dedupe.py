"""Remove duplicate and near-duplicate posts before they reach the pipeline.

Social platforms are full of repetition: copypasta, quote-tweets of the same sentence, bot posts
that differ only in a trailing link. Left in, a duplicate that lands in both the training and the
test split lets a model score itself on text it has memorised, which inflates accuracy on a small
corpus. Removing them at collection, rather than mid-pipeline, also keeps the record count
identical across every preprocessing configuration, so two runs stay comparable.

Two passes, cheapest first:

1. **Exact after normalisation.** Case, whitespace, links and mentions are stripped before
   hashing, so "same text, different link" collapses to one record. This catches most repetition.
2. **Near-duplicate by token overlap.** Jaccard similarity over the normalised token set, above
   ``threshold``. Candidates come from a *prefix filter*: two sets can only reach similarity
   ``t`` if they share one of the ``floor((1 - t) * size) + 1`` rarest tokens in the smaller set,
   so indexing just those tokens finds every real match while comparing a tiny fraction of the
   pairs. Indexing on frequency alone is not enough: in a corpus with a small vocabulary every
   token looks rare relative to the others, and the comparison degenerates to every pair.

The first occurrence is always the one kept, so the result does not depend on dictionary order.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal
from math import ceil

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Record

logger = get_logger(__name__)

# Links, mentions and punctuation vary between copies of the same text, so none of them take part
# in the comparison.
_NOISE = re.compile(r"https?://\S+|www\.\S+|@\w+")
_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)

DEFAULT_SIMILARITY = 0.9


def normalise(text: str) -> str:
    """Lower-case text with links, mentions and punctuation removed and whitespace collapsed."""
    stripped = _NON_WORD.sub(" ", _NOISE.sub(" ", text.lower()))
    return " ".join(stripped.split())


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = len(left | right)
    return len(left & right) / union if union else 0.0


def _prefix(tokens: frozenset[str], rank: dict[str, int], threshold: float) -> list[str]:
    """The rarest tokens that any sufficiently similar set must also contain.

    Two sets with Jaccard similarity at least ``threshold`` can differ by at most
    ``(1 - threshold)`` of the smaller set, so they must agree on one of its
    ``floor((1 - threshold) * size) + 1`` rarest tokens. Indexing only those is exact: it cannot
    miss a genuine match, and it keeps the candidate list short.
    """
    # Subtracting a float threshold first can turn 10 * (1 - 0.9) into 0.999999...,
    # losing a candidate token and missing pairs exactly at the similarity threshold.
    size = len(tokens) - ceil(Decimal(str(threshold)) * len(tokens)) + 1
    return sorted(tokens, key=lambda token: (rank[token], token))[:size]


def deduplicate(
    records: list[Record], threshold: float = DEFAULT_SIMILARITY
) -> tuple[list[Record], Counter[str]]:
    """Drop repeated posts, keeping the first occurrence of each.

    Args:
        records: Posts in the order the source returned them.
        threshold: Jaccard similarity at or above which two posts count as near-duplicates.
            1.0 disables the second pass, leaving only exact-after-normalisation matching.

    Returns:
        The records to keep, and a tally of what was dropped by reason (``duplicate``,
        ``near_duplicate``) suitable for merging into ``Dataset.filtered_out``.
    """
    kept: list[Record] = []
    dropped: Counter[str] = Counter()
    seen_exact: set[str] = set()
    normalised: list[frozenset[str]] = []

    # Corpus token frequencies give a stable rarest-first ordering for the prefix filter.
    frequency: Counter[str] = Counter()
    for record in records:
        frequency.update(set(normalise(record.text).split()))
    rank: dict[str, int] = dict(frequency)

    # Which kept records carry a given prefix token.
    by_token: defaultdict[str, list[int]] = defaultdict(list)

    for record in records:
        text = normalise(record.text)
        if text in seen_exact:
            dropped["duplicate"] += 1
            continue

        tokens = frozenset(text.split())
        prefix = _prefix(tokens, rank, threshold) if threshold < 1.0 and tokens else []
        if prefix:
            candidates = {index for token in prefix for index in by_token[token]}
            if any(_jaccard(tokens, normalised[index]) >= threshold for index in candidates):
                dropped["near_duplicate"] += 1
                continue

        seen_exact.add(text)
        position = len(kept)
        kept.append(record)
        normalised.append(tokens)
        for token in prefix:
            by_token[token].append(position)

    if dropped:
        logger.info("duplicates_removed", **dropped, kept=len(kept))
    return kept, dropped
