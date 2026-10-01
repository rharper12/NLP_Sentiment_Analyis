"""Consumer content screening, human decisions, and deterministic sample accounting.

Rules inspect original text, never sentiment predictions or author popularity. Suggestions
are deliberately conservative and always separate from operator decisions.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from sentiment_prep.errors import ValidationError
from sentiment_prep.models import (
    SENTIMENT_LABELS,
    AnalysisProgress,
    DatasetBundle,
    EligibilityDecision,
    EligibilityReason,
    EligibilityReview,
    Record,
    ScreeningSuggestion,
)
from sentiment_prep.storage.checkpoints import invalidate_checkpoints

CONSUMER_QUERY = (
    '("iPhone Duo" OR #iPhoneDuo OR "foldable iPhone" OR "folding iPhone") lang:en -is:retweet'
)
_PRODUCT = re.compile(r"\biphone\s*duo\b|\b(?:foldable|folding)\s+iphone\b", re.I)
_PERSONAL = re.compile(
    r"\b(?:i|i'm|i\u2019ve|i've|i\u2019d|i'd|my|me|we|our)\b|\b(?:love|hate|disappoint\w*|"
    r"excited|overpriced|beautiful|ugly|ridiculous|tempting|no thanks)\b",
    re.I,
)
_TECH = re.compile(r"\b(?:sdk|api|simulator|xcode|swiftui|code|coding|implementation)\b", re.I)
_TUTORIAL = re.compile(
    r"\b(?:tutorial|implement\w*|integrat\w*|debug\w*|build\w*|how to|sample code)\b", re.I
)
_NEWS = re.compile(
    r"\b(?:breaking|announces?|unveils?|launches?|reportedly|reports?:|read more|full story|via)\b",
    re.I,
)
_SOLICIT = re.compile(r"\b(?:follow|repost|retweet|enter|tag|subscribe)\b", re.I)
_PROMO = re.compile(r"\b(?:win|giveaway|prize|contest)\b", re.I)
_AD = re.compile(
    r"\b(?:use (?:my |our )?(?:code|referral)|affiliate link|shop now|buy now|sponsored)\b", re.I
)


def screen(record: Record) -> ScreeningSuggestion:
    """Suggest content eligibility with multiple signals for aggressive exclusions."""
    text = record.text
    personal = bool(_PERSONAL.search(text))
    if record.lang is not None and record.lang != "en":
        return ScreeningSuggestion(
            decision="exclude", reason="not_english", evidence=["Provider language is not English"]
        )
    if any(ref.type == "retweeted" for ref in record.references):
        return ScreeningSuggestion(
            decision="exclude",
            reason="duplicate_or_repeated_template",
            evidence=["Provider identifies a repost"],
        )
    if not _PRODUCT.search(text):
        return ScreeningSuggestion(
            decision="pending"
            if record.references or re.search(r"\bduo\b", text, re.I)
            else "exclude",
            reason="insufficient_context"
            if record.references or re.search(r"\bduo\b", text, re.I)
            else "off_topic",
            evidence=[
                "No explicit product phrase in the available text; related posts are not fetched"
            ],
        )
    if _PROMO.search(text) and _SOLICIT.search(text):
        return ScreeningSuggestion(
            decision="pending" if personal else "exclude",
            reason="giveaway_or_promotion",
            evidence=["Prize language combined with participation instructions"],
        )
    if _AD.search(text) and (
        record.urls or re.search(r"https?://|\bdiscount\b|\boff\b", text, re.I)
    ):
        return ScreeningSuggestion(
            decision="pending" if personal else "exclude",
            reason="giveaway_or_promotion",
            evidence=["Advertising call to action with a link or discount"],
        )
    if _TECH.search(text) and _TUTORIAL.search(text):
        return ScreeningSuggestion(
            decision="pending" if personal else "exclude",
            reason="technical_developer_content",
            evidence=["Implementation/tutorial language and technical tooling"],
        )
    if (
        not personal
        and _NEWS.search(text)
        and (
            record.urls
            or re.search(r"https?://|^breaking\b|\b(?:announces?|unveils?)\b", text, re.I)
        )
    ):
        return ScreeningSuggestion(
            decision="exclude",
            reason="news_or_article",
            evidence=["Headline/distribution language without a detected personal reaction"],
        )
    if record.lang is None:
        return ScreeningSuggestion(
            decision="pending",
            reason="insufficient_context",
            evidence=["Language metadata is unavailable; confirm English manually"],
        )
    if personal or "?" in text:
        return ScreeningSuggestion(
            decision="include",
            evidence=[
                "Product mention and personal reaction or question; confirm context manually"
            ],
        )
    return ScreeningSuggestion(
        decision="pending",
        reason="insufficient_context",
        evidence=["Content does not clearly establish a consumer reaction"],
    )


def screen_candidates(records: list[Record], threshold: float) -> list[Record]:
    """Annotate redundant text without deleting any candidate or changing human decisions.

    Only whitespace/case-identical complete text is a definite duplicate suggestion. Links,
    punctuation, word order, and all wording changes route matches to review instead.
    """
    # Source package exports import the adapter, which also consumes this policy.
    from sentiment_prep.sources.dedupe import duplicate_matches

    matches = duplicate_matches(records, threshold)
    by_id = {record.id: record for record in records}
    result = []
    for record in records:
        suggestion = record.screening or screen(record)
        if record.id in matches:
            original_id, _ = matches[record.id]
            identical = " ".join(record.text.casefold().split()) == " ".join(
                by_id[original_id].text.casefold().split()
            )
            suggestion = ScreeningSuggestion(
                decision="exclude" if identical else "pending",
                reason="duplicate_or_repeated_template",
                evidence=[
                    "Identical complete text after case/whitespace normalization"
                    if identical
                    else "Similar wording; meaning-changing differences require human review"
                ],
                duplicate_of=original_id,
            )
        result.append(record.model_copy(update={"screening": suggestion}))
    return result


def included_ids(bundle: DatasetBundle) -> set[str]:
    """Select earliest reviewed inclusions per known author; unknown authors stay independent."""
    policy = bundle.original.consumer_policy
    if policy is None:
        return {r.id for r in bundle.original.records}
    authors: Counter[str] = Counter()
    selected = set()
    for record in sorted(
        bundle.original.records,
        key=lambda r: (r.created_at or datetime.max.replace(tzinfo=UTC), r.id),
    ):
        if record.eligibility != "include" or not record.eligibility_reviewed:
            continue
        if record.author_id:
            if authors[record.author_id] >= policy.per_author_limit:
                continue
            authors[record.author_id] += 1
        selected.add(record.id)
    return selected


def reviewed_records(bundle: DatasetBundle) -> list[Record]:
    """Training examples require both independent human reviews; confidence is optional."""
    selected = included_ids(bundle)
    return [
        r
        for r in bundle.original.records
        if r.id in selected
        and r.eligibility == "include"
        and r.eligibility_reviewed
        and r.sentiment_reviewed
        and r.label in SENTIMENT_LABELS
    ]


class DayCoverage(BaseModel):
    """Actual coverage including empty days, expressed in the requested timezone."""

    day: str
    candidates: int = 0
    included: int = 0
    reviewed_final: int = 0


class ConsumerCounts(BaseModel):
    """Separate provider work, screening, human decisions, and usable training rows."""

    retrieved: int
    unique_records: int
    screened_candidates: int
    pending_eligibility: int
    human_inclusions: int
    human_exclusions: int
    included: int
    author_cap_held: int
    missing_author: int
    pending_sentiment: int
    reviewed_final: int
    reviewed_target: int
    shortfall: int
    days: list[DayCoverage]


def counts(bundle: DatasetBundle) -> ConsumerCounts:
    """Compute all selection counts from current decisions, never cached label totals."""
    policy = bundle.original.consumer_policy
    if policy is None:
        raise ValidationError("This dataset has no consumer-reaction policy")
    records = bundle.original.records
    selected = included_ids(bundle)
    final = {r.id for r in reviewed_records(bundle)}
    days = {}
    day = policy.start_date
    while day <= policy.end_date:
        key = day.isoformat()
        days[key] = DayCoverage(day=key)
        day += timedelta(days=1)
    for record in records:
        if record.created_at is not None:
            key = record.created_at.astimezone(ZoneInfo(policy.timezone)).date().isoformat()
            if key in days:
                days[key].candidates += 1
                days[key].included += record.id in selected
                days[key].reviewed_final += record.id in final
    human_inclusions = sum(r.eligibility_reviewed and r.eligibility == "include" for r in records)
    return ConsumerCounts(
        retrieved=bundle.collection.retrieved if bundle.collection else len(records),
        unique_records=len(bundle.collection.seen_ids) if bundle.collection else len(records),
        screened_candidates=sum(r.screening is not None for r in records),
        pending_eligibility=sum(
            not r.eligibility_reviewed or r.eligibility == "pending" for r in records
        ),
        human_inclusions=human_inclusions,
        human_exclusions=sum(
            r.eligibility_reviewed and r.eligibility == "exclude" for r in records
        ),
        included=len(selected),
        author_cap_held=human_inclusions - len(selected),
        missing_author=sum(not r.author_id for r in records),
        pending_sentiment=len(selected) - len(final),
        reviewed_final=len(final),
        reviewed_target=policy.reviewed_target,
        shortfall=max(0, policy.reviewed_target - len(final)),
        days=list(days.values()),
    )


class EligibilityItem(BaseModel):
    """An explicit operator confirmation; overrides must explain the changed suggestion."""

    id: str
    decision: EligibilityDecision
    reason: EligibilityReason | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def exclusion_reason(self) -> EligibilityItem:
        """An exclusion is incomplete without a stable reason."""
        if self.decision == "exclude" and self.reason is None:
            raise ValueError("Choose an exclusion reason")
        return self


def invalidate_derived(bundle: DatasetBundle) -> None:
    """Discard derived state while retaining originals, review history, and old snapshot files."""
    invalidate_checkpoints(bundle, "processed", "labelled")
    bundle.processed = None
    bundle.report = None
    bundle.analysis = AnalysisProgress()


def apply_decisions(bundle: DatasetBundle, items: list[EligibilityItem]) -> DatasetBundle:
    """Persist unchanged confirmations and corrections without treating suggestions as review."""
    if bundle.original.consumer_policy is None:
        raise ValidationError("Start a new Consumer reactions collection to use this review")
    updated = bundle.model_copy(deep=True)
    by_id = {r.id: r for r in updated.original.records}
    if any(item.id not in by_id for item in items):
        raise ValidationError("Review contains an unknown record ID")
    for item in items:
        record = by_id[item.id]
        if (
            record.screening
            and item.decision != "pending"
            and item.decision != record.screening.decision
            and not item.note.strip()
        ):
            raise ValidationError("Explain why you are overriding the screening suggestion")
        if record.eligibility != item.decision:
            record.sentiment_reviewed = False
            record.sentiment_reviewed_at = None
        record.eligibility = item.decision
        record.eligibility_reviewed = item.decision != "pending"
        record.eligibility_reason = item.reason
        record.eligibility_note = item.note.strip()
        record.eligibility_history.append(
            EligibilityReview(decision=item.decision, reason=item.reason, note=item.note.strip())
        )
    invalidate_derived(updated)
    selected = included_ids(updated)
    updated.review_ids = [i for i in updated.review_ids if i in selected]
    return updated
