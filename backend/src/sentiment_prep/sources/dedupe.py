"""Remove duplicate and near-duplicate posts before they reach the pipeline.

Social platforms are full of repetition: copypasta, quote-tweets of the same sentence, bot posts
that differ only in a trailing link. Left in, a duplicate that lands in both the training and the
test split lets a model score itself on text it has memorised, which inflates accuracy on a small
corpus. Removing them at collection gives later preprocessing runs the same original input;
individual steps can still remove records from their processed output.

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


def _negations(text: str) -> frozenset[str]:
    # Normalisation splits "don't" into "don t". Keep that marker as well as full words.
    return frozenset(text.split()) & {"no", "not", "never", "neither", "nor", "without", "t"}


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


def duplicate_matches(
    records: list[Record], threshold: float = DEFAULT_SIMILARITY
) -> dict[str, tuple[str, str]]:
    """Map repeated IDs to the first matching record and match kind, preserving originals.

    Args:
        records: Posts in the order the source returned them.
        threshold: Jaccard similarity at or above which two posts count as near-duplicates.
            1.0 disables the second pass, leaving only exact-after-normalisation matching.

    Returns:
        Record ID to (original ID, ``duplicate`` or ``near_duplicate``). Negation differences
        never qualify as a near match. Callers decide whether to remove or review matches.
    """
    kept: list[Record] = []
    matches: dict[str, tuple[str, str]] = {}
    seen_exact: dict[str, str] = {}
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
            matches[record.id] = (seen_exact[text], "duplicate")
            continue

        tokens = frozenset(text.split())
        prefix = _prefix(tokens, rank, threshold) if threshold < 1.0 and tokens else []
        if prefix:
            candidates = {index for token in prefix for index in by_token[token]}
            match = next(
                (
                    index
                    for index in sorted(candidates)
                    if _negations(text) == _negations(normalise(kept[index].text))
                    and _jaccard(tokens, normalised[index]) >= threshold
                ),
                None,
            )
            if match is not None:
                matches[record.id] = (kept[match].id, "near_duplicate")
                continue

        seen_exact[text] = record.id
        position = len(kept)
        kept.append(record)
        normalised.append(tokens)
        for token in prefix:
            by_token[token].append(position)

    return matches


def deduplicate(
    records: list[Record], threshold: float = DEFAULT_SIMILARITY
) -> tuple[list[Record], Counter[str]]:
    """Keep the first exact/near match; threshold equality remains inclusive."""
    matches = duplicate_matches(records, threshold)
    kept = [record for record in records if record.id not in matches]
    dropped = Counter(kind for _, kind in matches.values())
    if dropped:
        logger.info("duplicates_removed", **dropped, kept=len(kept))
    return kept, dropped
