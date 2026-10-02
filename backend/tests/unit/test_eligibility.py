"""Synthetic consumer screening and independent human-review contracts."""

from datetime import UTC, date, datetime

import pytest

from sentiment_prep.eligibility import (
    EligibilityItem,
    apply_decisions,
    counts,
    included_ids,
    reviewed_records,
    screen,
    screen_candidates,
)
from sentiment_prep.errors import ValidationError
from sentiment_prep.labeling.service import ManualLabel, apply_manual_labels
from sentiment_prep.models import ConsumerPolicy, Dataset, DatasetBundle, Record
from sentiment_prep.sources.dedupe import deduplicate


def record(text, **kwargs):
    return Record(id="one", text=text, source_type="x", lang="en", **kwargs)


def bundle(records, cap=2):
    return DatasetBundle(
        dataset_id="consumer-test",
        original=Dataset(
            records=records,
            source_type="x",
            consumer_policy=ConsumerPolicy(
                start_date=date(2026, 9, 9), end_date=date(2026, 9, 10), per_author_limit=cap
            ),
        ),
    )


@pytest.mark.parametrize(
    "text,decision,reason",
    [
        ("I love the iPhone Duo!", "include", None),
        ("I hate the iPhone Duo!", "include", None),
        ("The iPhone Duo is overpriced. No thanks.", "include", None),
        ("Apple announces iPhone Duo https://example.test/news", "exclude", "news_or_article"),
        (
            "Here is an iPhone Duo article. The price makes this a definite no for me. https://example.test",
            "include",
            None,
        ),
        ("Win an iPhone Duo! Follow and repost!", "exclude", "giveaway_or_promotion"),
        ("Win an iPhone Duo! Follow my account!", "pending", "giveaway_or_promotion"),
        ("I could only afford the iPhone Duo if I won a giveaway.", "include", None),
        (
            "Tutorial: build an iPhone Duo app with SwiftUI SDK simulator code",
            "exclude",
            "technical_developer_content",
        ),
        ("I'm a developer and I want an iPhone Duo for reading books.", "include", None),
        ("iPhone Duo https://example.test", "pending", "insufficient_context"),
        ("Would the folding iPhone fit in my pocket?", "include", None),
        ("The iPhone Duo has arrived", "pending", "insufficient_context"),
    ],
)
def test_suggestions_are_content_based_and_sentiment_symmetric(text, decision, reason):
    item = record(text)
    suggestion = screen(item)
    assert (suggestion.decision, suggestion.reason) == (decision, reason)
    assert suggestion.evidence
    assert not item.eligibility_reviewed


def test_missing_metadata_does_not_mean_approved():
    item = record("I love iPhone Duo").model_copy(update={"lang": None})
    assert screen(item).decision == "pending"
    assert not Record(
        id="old", text="old", source_type="csv", label_source="manual"
    ).sentiment_reviewed


@pytest.mark.parametrize(
    "text,decision",
    [
        ("I love this coffee maker!", "include"),
        ("I hate these running shoes!", "include"),
        ("Win a coffee maker! Follow and repost!", "exclude"),
        ("Brand announces running shoes https://example.test/news", "exclude"),
        ("Coffee maker https://example.test", "pending"),
    ],
)
def test_new_policy_screens_consumer_content_without_a_fixed_product(text, decision):
    suggestion = screen(record(text))
    assert suggestion.decision == decision
    assert suggestion.policy_version == "consumer-reactions-v2"


def test_legacy_product_policy_remains_explicit_and_resumable():
    item = record("I love this coffee maker!")
    legacy = screen(item, policy_version="consumer-reactions-v1")
    assert legacy.decision == "exclude" and legacy.reason == "off_topic"
    assert legacy.policy_version == "consumer-reactions-v1"
    screened = screen_candidates([item], 0.9, policy_version="consumer-reactions-v1")
    assert screened[0].screening == legacy
    assert (
        screen(record("I love the iPhone Duo"), policy_version="consumer-reactions-v1").decision
        == "include"
    )


def test_duplicates_remain_recoverable_and_opposites_survive():
    texts = [
        "I want the iPhone Duo.",
        "I don\u2019t want the iPhone Duo.",
        "I want the iPhone Duo.",
    ]
    rows = [record(text).model_copy(update={"id": str(i)}) for i, text in enumerate(texts)]
    screened = screen_candidates(rows, 0.5)
    assert len(screened) == 3
    assert screened[0].screening.decision == screened[1].screening.decision == "include"
    assert screened[2].screening.duplicate_of == "0"
    assert screened[2].screening.decision == "exclude"
    long = "I want the iPhone Duo because the larger display fits all of my reading needs"
    kept, _ = deduplicate(
        [
            record(long),
            record(long.replace("I want", "I do not want")).model_copy(update={"id": "two"}),
        ],
        0.8,
    )
    assert len(kept) == 2


def test_near_matches_require_review_even_at_threshold():
    a = "I want the iPhone Duo because this screen would be useful for reading books daily"
    rows = [record(a), record(a.replace("want", "hate")).model_copy(update={"id": "two"})]
    screened = screen_candidates(rows, 0.8)
    assert screened[1].screening.decision == "pending"
    assert screened[1].screening.duplicate_of == "one"


def test_author_limit_counts_only_human_inclusions_and_releases_capacity():
    rows = [
        record("I love iPhone Duo", author_id="author").model_copy(
            update={"id": str(i), "eligibility": "include", "eligibility_reviewed": True}
        )
        for i in range(3)
    ]
    rows += [
        record("I love iPhone Duo").model_copy(
            update={"id": str(i), "eligibility": "include", "eligibility_reviewed": True}
        )
        for i in range(3, 6)
    ]
    original = bundle(rows)
    assert included_ids(original) == {"0", "1", "3", "4", "5"}
    updated = apply_decisions(
        original, [EligibilityItem(id="0", decision="exclude", reason="off_topic")]
    )
    assert included_ids(updated) == {"1", "2", "3", "4", "5"}
    assert counts(updated).human_exclusions == 1
    assert counts(updated).missing_author == 3


def test_unchanged_confirmations_and_overrides_are_independent():
    row = record("I love iPhone Duo", created_at=datetime(2026, 9, 9, 7, tzinfo=UTC))
    row.screening = screen(row)
    original = bundle([row])
    assert counts(original).reviewed_final == 0
    updated = apply_decisions(original, [EligibilityItem(id="one", decision="include")])
    assert counts(updated).included == 1 and counts(updated).reviewed_final == 0
    updated = apply_manual_labels(updated, [ManualLabel(id="one", label="positive")])
    assert len(reviewed_records(updated)) == 1
    assert updated.original.records[0].label_confidence is None
    assert counts(updated).days[0].reviewed_final == 1
    assert counts(updated).days[1].candidates == 0
    with pytest.raises(ValidationError, match="Explain"):
        apply_decisions(
            updated, [EligibilityItem(id="one", decision="exclude", reason="news_or_article")]
        )
    updated = apply_decisions(
        updated,
        [
            EligibilityItem(
                id="one",
                decision="exclude",
                reason="news_or_article",
                note="Quoted headline, not a personal opinion",
            )
        ],
    )
    assert not reviewed_records(updated)
    assert len(updated.original.records[0].eligibility_history) == 2
    assert original.original.records[0].eligibility == "pending"
    with pytest.raises(ValidationError, match="eligibility first"):
        apply_manual_labels(updated, [ManualLabel(id="one", label="positive")])


def test_combined_review_keeps_both_decisions_and_preserves_automated_provenance():
    original = bundle(
        [record("I dislike this", comprehend_label="positive", comprehend_confidence=0.9)]
    )
    updated = apply_decisions(
        original, [EligibilityItem(id="one", decision="include", label="negative")]
    )
    saved = updated.original.records[0]
    assert saved.eligibility_reviewed and saved.sentiment_reviewed
    assert saved.label == "negative" and saved.label_source == "manual"
    assert saved.label_confidence is None and saved.sentiment_reviewed_at is not None
    assert saved.comprehend_label == "positive" and saved.comprehend_confidence == 0.9
    assert counts(updated).reviewed_final == 1
    assert not original.original.records[0].eligibility_reviewed
    excluded = apply_decisions(
        updated, [EligibilityItem(id="one", decision="exclude", reason="off_topic")]
    )
    assert counts(excluded).reviewed_final == 0
    assert excluded.original.records[0].text == original.original.records[0].text
    restored = apply_decisions(
        excluded, [EligibilityItem(id="one", decision="include", label="negative")]
    )
    assert counts(restored).reviewed_final == 1
    assert len(restored.original.records[0].eligibility_history) == 3


def test_combined_review_does_not_select_by_sentiment_or_lose_labels_on_author_holds():
    original = bundle(
        [
            record("first", author_id="same"),
            record("second", author_id="same").model_copy(update={"id": "two"}),
        ],
        cap=1,
    )
    updated = apply_decisions(
        original,
        [
            EligibilityItem(id="two", decision="include", label="positive"),
            EligibilityItem(id="one", decision="include", label="negative"),
        ],
    )
    assert included_ids(updated) == {"one"}
    assert counts(updated).author_cap_held == 1 and counts(updated).reviewed_final == 1
    released = apply_decisions(
        updated, [EligibilityItem(id="one", decision="exclude", reason="off_topic")]
    )
    assert included_ids(released) == {"two"}
    assert counts(released).reviewed_final == 1 and counts(released).pending_sentiment == 0


@pytest.mark.parametrize("decision", ["exclude", "pending"])
def test_non_inclusions_cannot_receive_a_label(decision):
    from pydantic import ValidationError as ModelValidationError

    with pytest.raises(ModelValidationError, match="Only included"):
        EligibilityItem(id="one", decision=decision, reason="off_topic", label="negative")


def test_duplicate_review_ids_are_rejected_without_mutating_original():
    original = bundle([record("first")])
    with pytest.raises(ValidationError, match="duplicate record"):
        apply_decisions(
            original,
            [
                EligibilityItem(id="one", decision="include", label="negative"),
                EligibilityItem(id="one", decision="exclude", reason="off_topic"),
            ],
        )
    assert not original.original.records[0].eligibility_history
