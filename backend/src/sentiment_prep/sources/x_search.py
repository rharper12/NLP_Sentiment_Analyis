"""X API v2 recent search adapter.

Facts that shape this module: recent search only returns the last seven days; every returned
post is billed (about $0.005 each); the endpoint pages via ``next_token`` at up to 100 posts
per page; rate limits arrive as HTTP 429 with an ``x-rate-limit-reset`` epoch header.

Only ``tweet.fields`` are requested. Expanding author objects bills a second read per post
and adds nothing to sentiment analysis. Every query is free-form so the tool works for any
topic; the adapter only appends language and retweet filters when the caller has not.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from sentiment_prep.errors import ExternalServiceError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record
from sentiment_prep.sources.spend_guard import SpendCapReachedError, SpendGuard

logger = get_logger(__name__)

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
    ) -> None:
        """Take a pooled client rather than building one.

        Args:
            guard: Enforces the per-fetch and per-day read caps.
            client: Shared ``httpx.Client`` bound to the X API base URL and carrying the bearer
                token. Owned by the caller; this class never closes it.
            allow_non_english: Keep posts whose ``lang`` is not ``en``.
        """
        self._guard = guard
        self._allow_non_english = allow_non_english
        self._client = client

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Dataset:
        """Page through recent search until ``limit`` records or a stop condition.

        Stop conditions (all return a partial dataset, none raise): no more pages, a spend cap,
        or a rate limit that outlasts the retry budget.
        """
        if not query:
            raise ValueError("X search requires a query")
        full_query = query if "lang:" in query else f"{query} {DEFAULT_QUERY_SUFFIX}"
        records: list[Record] = []
        filtered: Counter[str] = Counter()
        next_token: str | None = None
        truncated_reason: str | None = None
        started = time.perf_counter()
        logger.info("x_fetch_started", query=full_query, requested=limit)

        while len(records) < limit:
            if should_stop and should_stop():
                truncated_reason = "cancelled by client"
                break
            page_size = min(PAGE_SIZE, max(10, limit - len(records)))
            try:
                self._guard.reserve(page_size)
            except SpendCapReachedError as capped:
                truncated_reason = capped.reason
                logger.warning("x_fetch_truncated_by_cap", reason=capped.reason)
                break

            try:
                payload = self._get_page(full_query, page_size, next_token)
            except (httpx.HTTPError, ExternalServiceError):
                # Nothing was billed, so hand the reservation back before giving up.
                self._guard.release()
                raise
            if payload is None:
                self._guard.release()
                truncated_reason = "rate limited after retries"
                break

            posts = payload.get("data", [])
            self._guard.record(len(posts))
            kept, dropped = self._to_records(posts)
            records.extend(kept)
            filtered.update(dropped)
            next_token = payload.get("meta", {}).get("next_token")
            logger.debug("x_page_received", posts=len(posts), has_next=bool(next_token))
            if not next_token or not posts:
                break

        records = records[:limit]
        if truncated_reason is None and len(records) < limit:
            truncated_reason = "no more matching posts in the last 7 days"
        logger.info(
            "x_fetch_complete",
            requested=limit,
            returned=len(records),
            filtered_out=dict(filtered),
            reads_billed=self._guard.reads_this_fetch,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            truncated_reason=truncated_reason,
        )
        return Dataset(
            records=records,
            source_type="x",
            query=full_query,
            truncated_reason=truncated_reason,
            filtered_out=dict(filtered),
        )

    def usage(self) -> Json | None:
        """Best-effort call to ``GET /2/usage/tweets``.

        X exposes project-level usage on some tiers only; a 403 or 404 means the account cannot
        see it and the caller falls back to the local ledger. Never raises.
        """
        try:
            response = self._client.get("/usage/tweets")
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
        if next_token:
            params["next_token"] = next_token

        for attempt in range(1, MAX_RETRIES + 1):
            response = self._client.get("/tweets/search/recent", params=params)
            if response.status_code == 429:
                wait = self._backoff_seconds(response, attempt)
                logger.warning("x_rate_limited", attempt=attempt, wait_seconds=wait)
                time.sleep(wait)
                continue
            self._raise_for_status(response)
            data: Json = response.json()
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

    def _to_records(self, posts: list[Json]) -> tuple[list[Record], Counter[str]]:
        """Convert a page of posts, returning the kept records and a tally of what was dropped."""
        records: list[Record] = []
        dropped: Counter[str] = Counter()
        for post in posts:
            text = str(post.get("text", "")).strip()
            if not self._allow_non_english and post.get("lang") not in (None, "en"):
                dropped["not_english"] += 1
                continue
            if len(content_tokens(text)) < MIN_CONTENT_TOKENS:
                dropped["no_content_after_cleaning"] += 1
                continue
            created = post.get("created_at")
            records.append(
                Record(
                    id=str(post["id"]),
                    text=text,
                    source_type="x",
                    created_at=(
                        datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
                    ),
                )
            )
        return records, dropped
