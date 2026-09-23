"""Behaviour of duplicate removal: what is dropped, what survives, and in which order."""

from collections import Counter

import pytest

from sentiment_prep.models import Record
from sentiment_prep.sources.dedupe import deduplicate, normalise


def posts(*texts: str) -> list[Record]:
    return [Record(id=str(i), text=t, source_type="x") for i, t in enumerate(texts)]


def test_identical_posts_collapse_to_the_first_occurrence():
    """Copypasta split across a train/test boundary would let a model score memorised text."""
    kept, dropped = deduplicate(posts(*(["the same viral take on the trial"] * 5)))
    assert [r.id for r in kept] == ["0"]
    assert dropped == {"duplicate": 4}


def test_same_text_with_different_links_and_mentions_is_one_post():
    """Quote-tweets and bot reposts differ only in the parts that carry no sentiment."""
    kept, dropped = deduplicate(
        posts(
            "@alice this verdict is indefensible https://t.co/aaa",
            "@bob this verdict is indefensible https://t.co/zzz",
            "THIS VERDICT IS INDEFENSIBLE!!!",
        )
    )
    assert [r.id for r in kept] == ["0"]
    assert dropped == {"duplicate": 2}


def test_near_duplicates_are_dropped_but_genuinely_different_posts_are_kept():
    """Bot reposts differ by a word or two in an otherwise identical block of text."""
    base = (
        "the coverage of this trial has been relentless and unfair and the reporting "
        "has ignored every inconvenient detail from the first day onwards"
    )
    kept, dropped = deduplicate(
        posts(
            base,
            base.replace("onwards", "onward"),
            "i thought the documentary about the trial was carefully made",
        )
    )
    assert [r.id for r in kept] == ["0", "2"]
    assert dropped == {"near_duplicate": 1}


def test_negation_variants_are_never_collapsed():
    """A one-word difference can invert sentiment, so short posts must not be merged.

    This is why the threshold is high: at 0.9 a pair must be near-identical over a long span.
    "this is good" and "this is not good" overlap by 0.75, and collapsing them would silently
    delete the negative half of the corpus.
    """
    kept, dropped = deduplicate(posts("this verdict is good", "this verdict is not good"))
    assert len(kept) == 2 and not dropped


def test_threshold_of_one_keeps_near_duplicates():
    """Exact-only matching is available for corpora where small edits are meaningful."""
    kept, dropped = deduplicate(
        posts(
            "the coverage of this trial has been relentless and unfair all week",
            "the coverage of this trial has been relentless and unfair all month",
        ),
        threshold=1.0,
    )
    assert len(kept) == 2 and not dropped


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("size,threshold", [(10, 0.9), (20, 0.95), (100, 0.99)])
def test_near_duplicate_exactly_at_threshold_is_not_lost_to_rounding(reverse, size, threshold):
    shorter = " ".join(f"word{i}" for i in range(size - 1))
    texts = [shorter, f"{shorter} extra"]
    if reverse:
        texts.reverse()
    kept, dropped = deduplicate(posts(*texts), threshold=threshold)
    assert [r.id for r in kept] == ["0"]
    assert dropped == {"near_duplicate": 1}


def test_short_posts_sharing_common_words_are_not_treated_as_duplicates():
    """Only uncommon tokens propose candidates, so ordinary phrasing does not collapse posts."""
    kept, _ = deduplicate(posts("i love it", "i hate it", "i saw it"))
    assert len(kept) == 3


def test_nothing_is_dropped_when_there_is_nothing_to_drop():
    kept, dropped = deduplicate(posts("first distinct opinion", "second distinct opinion"))
    assert len(kept) == 2 and dropped == Counter()


def test_normalise_ignores_case_punctuation_links_and_mentions():
    assert normalise("@user  It's OVER!! https://t.co/x") == "it s over"
