"""X API v2 recent and full-archive search adapter.

Older windows use full-archive search for pay-per-use accounts. Both endpoints share the
spend guard and use pages of at most 100 posts. Rate limits arrive as HTTP 429 with an
``x-rate-limit-reset`` epoch header; archive requests are paced at one per second per fetch.

Only ``tweet.fields`` are requested. Author expansions can incur additional resource charges;
the post's author ID suffices for sampling. Every query is free-form so the tool works for any
topic; the adapter only appends language and retweet filters when the caller has not.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import httpx

from sentiment_prep.budget import (
    CALL_AND_SAVE_SECONDS,
    BudgetExhaustedError,
    can_start,
    current_budget,
    require_budget,
)
from sentiment_prep.eligibility import screen_candidates
from sentiment_prep.errors import ExternalServiceError, ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import (
    CollectionProgress,
    CollectionSlice,
    ConsumerPolicy,
    Dataset,
    Record,
    calendar_bounds,
)
from sentiment_prep.sources.dedupe import deduplicate
from sentiment_prep.sources.payloads import XPage, XPost
from sentiment_prep.sources.spend_guard import SpendCapReachedError, SpendGuard

logger = get_logger(__name__)

# Recent search reaches back seven days; older windows require the full archive.
SEARCH_WINDOW = timedelta(days=7)
ARCHIVE_START = datetime(2006, 3, 1, tzinfo=UTC)
END_TIME_LAG = timedelta(seconds=30)
PAGE_SIZE = 100
# Apply the minimum content length at collection so later preprocessing runs share one input.
# This heuristic can exclude meaningful short posts; it does not establish relevance or quality.
MIN_CONTENT_TOKENS = 5
MAX_RETRIES = 3
DEFAULT_QUERY_SUFFIX = "lang:en -is:retweet"

Json = dict[str, Any]


class XQueryError(ExternalServiceError):
    """The saved request was rejected; retrying the identical query/cursor cannot repair it."""


# Exclude links and mentions from this length check without changing the stored original text.
_NOISE = re.compile(r"https?://\S+|www\.\S+|@\w+")


def clamp_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Validate archive bounds and keep the end away from the present instant.

    Args:
        start: Requested oldest post, or ``None`` for "as far back as possible".
        end: Requested newest post, or ``None`` for "up to now".

    Returns:
        UTC bounds with historical dates preserved and a near-present end moved back slightly.

    Raises:
        ValidationError: if the range runs backwards. Clamping cannot repair that, and sending it
            draws an opaque 400 from X that would be reported as a bad query.
    """
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ValidationError("search dates must include a timezone")
    now = datetime.now(UTC)
    latest = now - END_TIME_LAG
    if start is not None:
        start = start.astimezone(UTC)
        if start < ARCHIVE_START:
            raise ValidationError("X's searchable archive starts in March 2006")
    if end is not None:
        end = min(end.astimezone(UTC), latest)
        if end <= ARCHIVE_START:
            raise ValidationError("X's searchable archive starts in March 2006")
    if start is not None and start >= (end or latest):
        raise ValidationError("the start of the range must come before its end and the present")
    return start, end


def content_tokens(text: str) -> list[str]:
    """Words left once links and mentions are removed.

    Used to decide whether a post says anything at all, rather than counting a link as content.
    """
    return _NOISE.sub(" ", text).split()


class XSearchSource:
    """Fetch posts matching a query from the selected window, within spend caps."""

    name = "x"

    def __init__(
        self,
        guard: SpendGuard,
        client: httpx.Client,
        allow_non_english: bool = False,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Take a pooled client rather than building one.

        Args:
            guard: Enforces the per-fetch and per-day read caps.
            client: Shared ``httpx.Client`` bound to the X API base URL and carrying the bearer
                token. Owned by the caller; this class never closes it.
            allow_non_english: Keep posts whose ``lang`` is not ``en``.
            start_time: Oldest post to return. Older windows automatically use full-archive search.
            end_time: Newest post to return. Clamped to a few seconds ago, because X rejects a
                window that runs up to the present instant.
        """
        self._guard = guard
        self._allow_non_english = allow_non_english
        self._client = client
        self._start, self._end = clamp_window(start_time, end_time)
        horizon = datetime.now(UTC) - SEARCH_WINDOW
        self._mode: Literal["recent", "all"] = (
            "all"
            if any(t is not None and t < horizon for t in (self._start, self._end))
            else "recent"
        )
        # An end-only historical request has a bounded, explicit seven-day window.
        if self._mode == "all" and self._start is None and self._end is not None:
            self._start = max(ARCHIVE_START, self._end - SEARCH_WINDOW)
        self._next_archive_call = 0.0
        self._retry_at = 0.0
        self._consumer = False

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
        *,
        resume: Dataset | None = None,
        progress: CollectionProgress | None = None,
        persist: Callable[[Dataset, CollectionProgress], None] | None = None,
        dedupe_similarity: float | None = None,
        consumer_policy: ConsumerPolicy | None = None,
    ) -> Dataset:
        """Persist each page with its cursor before spending on the next page.

        A lost transport response may have been billed. Keep that reservation conservatively;
        resuming its cursor may repeat that ambiguous page, never a successfully committed page.
        When dedupe_similarity is set, the target counts retained unique posts after each page.
        """
        if not query:
            raise ValidationError("X search requires a query")
        full_query = query if "lang:" in query else f"{query} {DEFAULT_QUERY_SUFFIX}"
        self._consumer = consumer_policy is not None
        if progress is not None:
            # Freeze the window across cursor resumptions, including a default recent search.
            now = datetime.now(UTC)
            saved = (
                resume if progress.search_mode or progress.next_token or progress.reads else None
            )
            self._start = (
                (saved.window_start if saved is not None else None)
                or self._start
                or now - SEARCH_WINDOW + timedelta(minutes=1)
            )
            self._end = (
                (saved.window_end if saved is not None else None) or self._end or now - END_TIME_LAG
            )
        state = (
            progress.model_copy(deep=True)
            if progress
            else CollectionProgress(request={}, billed_reads=0, committed_cost_usd=Decimal("0"))
        )
        if state.search_mode is not None:
            self._mode = state.search_mode
        elif state.next_token or state.reads:
            # Jobs saved before archive support always used recent search.
            self._mode = "recent"
        state.search_mode = self._mode
        records = list(resume.records) if resume is not None else []
        if consumer_policy and not state.slices:
            state.slices = calendar_slices(consumer_policy)
        if consumer_policy:
            limit = state.candidate_target or limit
        seen = set(state.seen_ids) | {record.id for record in records}
        filtered = Counter(resume.filtered_out if resume is not None else {})
        # Older partial jobs may still contain duplicates. Seed the next page with unique rows.
        if dedupe_similarity is not None and not consumer_policy:
            records, dropped = deduplicate(records, dedupe_similarity)
            filtered.update(dropped)
        # Older jobs reached the raw target and were then deduplicated by the route. Their
        # saved cursor still points at unread pages; reopen them without replaying billed pages.
        if consumer_policy:
            state.complete = state.terminal_error or not pending_slices(state, records, limit)
        elif len(records) >= limit:
            state.complete = True
            state.retry_at = 0
        elif state.needs_more_records(len(records)):
            state.complete = False
        # Expiry matters only if more provider work is needed. A satisfied target is reusable
        # even after its old recent-search cursor expires.
        if (
            not state.complete
            and self._mode == "recent"
            and self._start is not None
            and self._start < datetime.now(UTC) - SEARCH_WINDOW
        ):
            raise ValidationError(
                "This saved recent search has expired. Start a new search with the same dates "
                "to use the full archive."
            )
        self._guard.reads_this_fetch = state.reads
        self._retry_at = state.retry_at
        if not state.terminal_error:
            state.stop_reason = None
        if consumer_policy and state.complete and not state.terminal_error and len(records) < limit:
            state.stop_reason = "no more matching posts in the selected window"

        def snapshot() -> Dataset:
            start, end = consumer_policy.bounds() if consumer_policy else (self._start, self._end)
            return Dataset(
                records=records if consumer_policy else records[:limit],
                source_type="x",
                query=full_query,
                window_start=start,
                window_end=end,
                truncated_reason=state.stop_reason,
                filtered_out=dict(filtered),
                consumer_policy=consumer_policy,
                fetched_at=resume.fetched_at if resume is not None else datetime.now(UTC),
            )

        def save() -> None:
            if persist is not None:
                persist(snapshot(), state)

        if progress is not None and (
            progress.search_mode is None
            or state.complete != progress.complete
            or (resume is not None and records != resume.records)
        ):
            save()  # Persist recovered rows/completion and freeze dates before any paid call.

        while not state.complete and (consumer_policy or len(records) < limit):
            active_slice = None
            remaining = limit - len(records)
            if consumer_policy:
                pending = pending_slices(state, records, limit)
                if not pending:
                    state.complete = True
                    break
                active_slice, remaining = pending[0]
                self._start, self._end = active_slice.start, active_slice.end
            if should_stop and should_stop():
                state.stop_reason = "cancelled by client"
                break
            if time.time() < state.retry_at:
                state.stop_reason = "rate limited; retry after the indicated time"
                break
            if not can_start():
                state.retry_at = 0
                state.stop_reason = "request budget reached; resume to continue"
                break
            page_size = min(PAGE_SIZE, max(10, remaining))
            try:
                self._guard.reserve(page_size)
            except SpendCapReachedError as capped:
                state.stop_reason = capped.reason
                break
            try:
                payload = self._get_page(
                    full_query,
                    page_size,
                    active_slice.next_token if active_slice else state.next_token,
                )
            except BudgetExhaustedError:
                self._guard.release()
                state.retry_at = 0
                state.stop_reason = "request budget reached; resume to continue"
                break
            except httpx.TransportError:
                # X may have processed the request: keep the daily reservation and per-job cap.
                state.reads += page_size
                state.stop_reason = "X transport failure; the last page may have been billed"
                if persist is None and not records:
                    raise
                break
            except (httpx.HTTPError, ExternalServiceError) as exc:
                self._guard.release()
                detail = str(exc) if isinstance(exc, ExternalServiceError) else "X request failed"
                if isinstance(exc, XQueryError):
                    state.complete = True
                    state.terminal_error = True
                    state.retry_at = 0
                    state.stop_reason = (
                        f"{detail}; successful pages saved; "
                        "correct the request and start a new search"
                    )
                    save()
                else:
                    state.stop_reason = f"{detail}; successful pages saved; resume to retry"
                if persist is None and not records:
                    raise
                break
            if payload is None:
                self._guard.release()
                state.retry_at = self._retry_at
                state.stop_reason = "rate limited; resume after retry time"
                break
            try:
                page = XPage.model_validate(payload)
            except ValueError:
                # A successful HTTP response may have been billed despite unusable contents.
                state.reads += page_size
                state.stop_reason = (
                    "X returned malformed posts; successful pages saved; "
                    "last page may have been billed"
                )
                save()
                if persist is None and not records:
                    raise ExternalServiceError("X returned malformed posts") from None
                break
            posts = page.data
            state.retrieved += len(posts)
            state.reads = self._guard.reads_this_fetch + len(posts)
            kept, dropped = self._to_records(posts)
            # Overlapping provider pages can repeat a post; its external ID remains unchanged.
            retained_ids = {record.id for record in records}
            for record in kept:
                if record.id not in retained_ids:
                    records.append(record)
                    retained_ids.add(record.id)
            seen.update(post.id for post in posts)
            state.seen_ids = sorted(seen)
            filtered.update(dropped)
            if consumer_policy:
                records = screen_candidates(records, consumer_policy.duplicate_threshold)
            elif dedupe_similarity is not None:
                records, dropped = deduplicate(records, dedupe_similarity)
                filtered.update(dropped)
            if active_slice:
                active_slice.next_token = page.meta.next_token
                active_slice.exhausted = not page.meta.next_token
            else:
                state.next_token = page.meta.next_token
            state.retry_at = 0
            state.complete = (
                not pending_slices(state, records, limit)
                if consumer_policy
                else not state.next_token or len(records) >= limit
            )
            if state.complete and len(records) < limit:
                state.stop_reason = (
                    "no more matching posts in the selected window"
                    if self._start or self._end
                    else "no more matching posts in the last 7 days"
                )
            save()  # A failure here must stop; never fetch another paid page.
            committed = self._guard.record(len(posts))
            if state.committed_cost_usd is not None:
                state.committed_cost_usd += committed
            if state.billed_reads is not None:
                state.billed_reads += len(posts)
            save()
            logger.debug(
                "x_page_received",
                returned=len(posts),
                retained=min(len(records), limit),
                reads_billed=state.billed_reads,
                filtered_out=dict(filtered),
                has_more=bool(state.next_token),
            )
        if not state.complete:
            save()
        logger.info(
            "x_fetch_complete",
            requested=limit,
            returned=min(len(records), limit),
            reads_billed=state.billed_reads,
            filtered_out=dict(filtered),
            complete=state.complete,
            has_more=bool(state.next_token),
            truncated_reason=state.stop_reason,
        )
        return snapshot()

    def usage(self) -> Json | None:
        """Best-effort call to ``GET /2/usage/tweets``.

        X exposes project-level usage on some tiers only; a 403 or 404 means the account cannot
        see it and the caller falls back to the local ledger. Never raises.
        """
        try:
            if not can_start():
                return None
            response = self._client.get("/usage/tweets", timeout=3.0)
        except httpx.HTTPError as exc:
            logger.warning("x_usage_unavailable", error=str(exc))
            return None
        if response.status_code != 200:
            logger.info("x_usage_not_exposed", status=response.status_code)
            return None
        data: Json = response.json().get("data", {})
        logger.info("x_usage_fetched", keys=sorted(data))
        return data

    def _get_page(self, query: str, page_size: int, next_token: str | None) -> Json | None:
        params: dict[str, str | int] = {
            "query": query,
            "max_results": page_size,
            "tweet.fields": "id,text,created_at,lang",
        }
        if self._consumer:
            params["tweet.fields"] = (
                "id,text,created_at,lang,author_id,conversation_id,in_reply_to_user_id,"
                "referenced_tweets,entities,note_tweet"
            )
            params["sort_order"] = "recency"
        # RFC 3339 with a Z suffix is the only format the endpoint accepts.
        if self._start:
            params["start_time"] = self._start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self._end:
            params["end_time"] = self._end.strftime("%Y-%m-%dT%H:%M:%SZ")
        if next_token:
            params["next_token"] = next_token

        for attempt in range(1, MAX_RETRIES + 1):
            require_budget()
            if self._mode == "all":
                wait = max(0.0, self._next_archive_call - time.monotonic())
                budget = current_budget.get()
                if budget is not None and not budget.available(wait + CALL_AND_SAVE_SECONDS):
                    raise BudgetExhaustedError("Archive pacing exceeds the remaining request time")
                if wait:
                    time.sleep(wait)
                self._next_archive_call = time.monotonic() + 1.0
            response = self._client.get(f"/tweets/search/{self._mode}", params=params, timeout=3.0)
            if response.status_code == 429:
                wait = self._backoff_seconds(response, attempt)
                logger.warning("x_rate_limited", attempt=attempt, wait_seconds=wait)
                self._retry_at = time.time() + wait
                budget = current_budget.get()
                if attempt == MAX_RETRIES or (
                    budget is not None and not budget.available(wait + CALL_AND_SAVE_SECONDS)
                ):
                    return None
                time.sleep(wait)
                continue
            self._raise_for_status(response)
            try:
                data: Json = response.json()
            except ValueError:
                # Hand malformed successful bodies to the validation boundary, retaining spend.
                return {}
            return data
        return None

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Turn an X error response into a message the person can act on.

        Without this an expired token surfaces as a generic 500 "internal error", which tells the
        operator nothing. The upstream status is preserved in the text; the token never is.
        """
        if response.is_success:
            return
        status = response.status_code
        if status == 403 and response.request.url.path.endswith("/search/all"):
            detail = (
                "X denied full-archive access. Check that this bearer token belongs to an app "
                "with pay-per-use or Enterprise full-archive access."
            )
        elif status in (401, 403):
            detail = "X rejected the credentials. Check the bearer token and the app's permissions."
        elif status == 402:
            detail = "X reports the account cannot make this request. Check the credit balance."
        elif status == 400:
            detail = "X rejected the query. Check the search operators."
        elif status >= 500:
            detail = "X is unavailable. Try again shortly."
        else:
            detail = "X refused the request."
        logger.error("x_request_failed", status=status)
        if 400 <= status < 500 and status != 429:
            raise XQueryError(f"{detail} (X API returned HTTP {status})")
        raise ExternalServiceError(f"{detail} (X API returned HTTP {status})")

    @staticmethod
    def _backoff_seconds(response: httpx.Response, attempt: int) -> float:
        reset = response.headers.get("x-rate-limit-reset")
        if reset and reset.isdigit():
            return max(1.0, min(60.0, int(reset) - time.time()))
        return float(2**attempt)

    def _to_records(self, posts: list[XPost]) -> tuple[list[Record], Counter[str]]:
        """Convert a page of posts, returning the kept records and a tally of what was dropped."""
        records: list[Record] = []
        dropped: Counter[str] = Counter()
        for post in posts:
            consumer = self._consumer
            text = post.note_tweet.text if post.note_tweet else post.text
            if consumer and post.created_at is None:
                dropped["missing_timestamp"] += 1
                continue
            if post.created_at is not None and (
                (self._start is not None and post.created_at < self._start)
                or (self._end is not None and post.created_at >= self._end)
            ):
                dropped["out_of_window"] += 1
                continue
            if not consumer and not self._allow_non_english and post.lang not in (None, "en"):
                dropped["not_english"] += 1
                continue
            if not consumer and len(content_tokens(text)) < MIN_CONTENT_TOKENS:
                dropped["no_content_after_cleaning"] += 1
                continue
            records.append(
                Record(
                    id=post.id,
                    text=text,
                    source_type="x",
                    created_at=post.created_at,
                    lang=post.lang,
                    author_id=post.author_id,
                    conversation_id=post.conversation_id,
                    in_reply_to_user_id=post.in_reply_to_user_id,
                    references=post.referenced_tweets,
                    urls=(post.note_tweet.entities.urls if post.note_tweet else post.entities.urls),
                )
            )
        return records, dropped


def calendar_slices(policy: ConsumerPolicy) -> list[CollectionSlice]:
    """One independent cursor per requested local day, oldest day first."""
    day = policy.start_date
    result = []
    while day <= policy.end_date:
        start, end = calendar_bounds(day, day, policy.timezone)
        result.append(CollectionSlice(day=day, start=start, end=end))
        day += timedelta(days=1)
    return result


def pending_slices(
    state: CollectionProgress, records: list[Record], target: int
) -> list[tuple[CollectionSlice, int]]:
    """Equal candidate quotas, with the remainder assigned to earlier days; no gap backfill."""
    result = []
    for index, block in enumerate(state.slices):
        quota = target // len(state.slices) + (index < target % len(state.slices))
        count = sum(
            r.created_at is not None and block.start <= r.created_at < block.end for r in records
        )
        if not block.exhausted and count < quota:
            result.append((block, quota - count))
    return result
