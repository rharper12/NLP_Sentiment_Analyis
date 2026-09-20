"""X API v2 recent search adapter.

Facts that shape this module: recent search only returns the last seven days, and a caller may
narrow that with ``start_time``/``end_time``; every returned post is billed (about $0.005 each);
the endpoint pages via ``next_token`` at up to 100 posts per page; rate limits arrive as HTTP 429
with an ``x-rate-limit-reset`` epoch header.

Only ``tweet.fields`` are requested. Expanding author objects bills a second read per post
and adds nothing to sentiment analysis. Every query is free-form so the tool works for any
topic; the adapter only appends language and retweet filters when the caller has not.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from sentiment_prep.budget import CALL_AND_SAVE_SECONDS, can_start, current_budget
from sentiment_prep.errors import ExternalServiceError, ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import CollectionProgress, Dataset, Record
from sentiment_prep.sources.payloads import XPage, XPost
from sentiment_prep.sources.spend_guard import SpendCapReachedError, SpendGuard

logger = get_logger(__name__)

# Recent search reaches back seven days. X rejects a start_time older than that, and an end_time
# newer than about ten seconds ago, so both are clamped before the request rather than after.
SEARCH_WINDOW = timedelta(days=7)
END_TIME_LAG = timedelta(seconds=30)
PAGE_SIZE = 100
# Minimum *content* tokens: a post whose length is made up of a link and a mention has nothing to
# classify, and would be emptied by the cleaning steps anyway. Filtering here rather than mid-
# pipeline keeps the record count identical across every preprocessing configuration, so two runs
# can be compared against the same denominator.
MIN_CONTENT_TOKENS = 5
MAX_RETRIES = 3
DEFAULT_QUERY_SUFFIX = "lang:en -is:retweet"

Json = dict[str, Any]

# Links and mentions are stripped before measuring length, for the same reason the punctuation
# step removes them later: neither carries sentiment.
_NOISE = re.compile(r"https?://\S+|www\.\S+|@\w+")


def clamp_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Bring a requested window inside what recent search can serve.

    Args:
        start: Requested oldest post, or ``None`` for "as far back as possible".
        end: Requested newest post, or ``None`` for "up to now".

    Returns:
        The window to send. A start older than the seven-day horizon is moved to the horizon, and
        an end too close to the present is moved back, so X never rejects the request outright.

    Raises:
        ValidationError: if the range runs backwards. Clamping cannot repair that, and sending it
            draws an opaque 400 from X that would be reported as a bad query.
    """
    if start is not None and end is not None and start >= end:
        raise ValidationError("the start of the range must be before its end")
    now = datetime.now(UTC)
    horizon = now - SEARCH_WINDOW + timedelta(minutes=1)
    latest = now - END_TIME_LAG
    if start is not None:
        start = max(start.astimezone(UTC), horizon)
    if end is not None:
        end = min(end.astimezone(UTC), latest)
    return start, end


def content_tokens(text: str) -> list[str]:
    """Words left once links and mentions are removed.

    Used to decide whether a post says anything at all, rather than counting a link as content.
    """
    return _NOISE.sub(" ", text).split()


class XSearchSource:
    """Fetch recent posts matching a query, within spend caps."""

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
            start_time: Oldest post to return. Clamped to seven days ago, which is as far back as
                recent search reaches.
            end_time: Newest post to return. Clamped to a few seconds ago, because X rejects a
                window that runs up to the present instant.
        """
        self._guard = guard
        self._allow_non_english = allow_non_english
        self._client = client
        self._start, self._end = clamp_window(start_time, end_time)
        self._retry_at = 0.0

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
        *,
        resume: Dataset | None = None,
        progress: CollectionProgress | None = None,
        persist: Callable[[Dataset, CollectionProgress], None] | None = None,
    ) -> Dataset:
        """Persist each page with its cursor before spending on the next page.

        A lost transport response may have been billed. Keep that reservation conservatively;
        resuming its cursor may repeat that ambiguous page, never a successfully committed page.
        """
        if not query:
            raise ValidationError("X search requires a query")
        full_query = query if "lang:" in query else f"{query} {DEFAULT_QUERY_SUFFIX}"
        if progress is not None:
            # Freeze the window across cursor resumptions, including a default recent search.
            now = datetime.now(UTC)
            self._start = (
                (resume.window_start if resume is not None else None)
                or self._start
                or now - SEARCH_WINDOW + timedelta(minutes=1)
            )
            self._end = (
                (resume.window_end if resume is not None else None)
                or self._end
                or now - END_TIME_LAG
            )
        state = (
            progress.model_copy(deep=True)
            if progress
            else CollectionProgress(request={}, billed_reads=0, committed_cost_usd=Decimal("0"))
        )
        records = list(resume.records) if resume is not None else []
        filtered = Counter(resume.filtered_out if resume is not None else {})
        self._guard.reads_this_fetch = state.reads
        self._retry_at = state.retry_at
        state.stop_reason = None

        def snapshot() -> Dataset:
            return Dataset(
                records=records[:limit],
                source_type="x",
                query=full_query,
                window_start=self._start,
                window_end=self._end,
                truncated_reason=state.stop_reason,
                filtered_out=dict(filtered),
            )

        def save() -> None:
            if persist is not None:
                persist(snapshot(), state)

        while not state.complete and len(records) < limit:
            if should_stop and should_stop():
                state.stop_reason = "cancelled by client"
                break
            if not can_start():
                state.stop_reason = "request budget reached; resume to continue"
                break
            if time.time() < state.retry_at:
                state.stop_reason = "rate limited; retry after the indicated time"
                break
            page_size = min(PAGE_SIZE, max(10, limit - len(records)))
            try:
                self._guard.reserve(page_size)
            except SpendCapReachedError as capped:
                state.stop_reason = capped.reason
                break
            try:
                payload = self._get_page(full_query, page_size, state.next_token)
            except httpx.TransportError:
                # X may have processed the request: keep the daily reservation and per-job cap.
                state.reads += page_size
                state.stop_reason = "X transport failure; the last page may have been billed"
                if persist is None and not records:
                    raise
                break
            except (httpx.HTTPError, ExternalServiceError):
                self._guard.release()
                state.stop_reason = "X request failed; successful pages saved; resume to retry"
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
            state.reads = self._guard.reads_this_fetch + len(posts)
            kept, dropped = self._to_records(posts)
            # Overlapping provider pages can repeat a post; its external ID remains unchanged.
            seen = {record.id for record in records}
            for record in kept:
                if record.id not in seen:
                    records.append(record)
                    seen.add(record.id)
            filtered.update(dropped)
            state.next_token = page.meta.next_token
            state.retry_at = 0
            state.complete = not state.next_token or not posts or len(records) >= limit
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
        if not state.complete:
            save()
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
        # RFC 3339 with a Z suffix is the only format the endpoint accepts.
        if self._start:
            params["start_time"] = self._start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self._end:
            params["end_time"] = self._end.strftime("%Y-%m-%dT%H:%M:%SZ")
        if next_token:
            params["next_token"] = next_token

        for attempt in range(1, MAX_RETRIES + 1):
            if not can_start():
                return None
            response = self._client.get("/tweets/search/recent", params=params, timeout=3.0)
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
        if status in (401, 403):
            detail = "X rejected the credentials. Check the bearer token and the app's permissions."
        elif status == 402:
            detail = "X reports the account cannot make this request. Check the credit balance."
        elif status == 400:
            detail = "X rejected the query. Check the search operators."
        elif status >= 500:
            detail = "X is unavailable. Try again shortly."
        else:
            detail = "X refused the request."
        logger.error("x_request_failed", status=status, body=response.text[:200])
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
            text = post.text.strip()
            if not self._allow_non_english and post.lang not in (None, "en"):
                dropped["not_english"] += 1
                continue
            if len(content_tokens(text)) < MIN_CONTENT_TOKENS:
                dropped["no_content_after_cleaning"] += 1
                continue
            records.append(
                Record(
                    id=post.id,
                    text=text,
                    source_type="x",
                    created_at=post.created_at,
                )
            )
        return records, dropped
