"""X API v2 recent search adapter.

Facts that shape this module: recent search only returns the last seven days; every returned
post is billed (about $0.005 each); the endpoint pages via ``next_token`` at up to 100 posts
per page; rate limits arrive as HTTP 429 with an ``x-rate-limit-reset`` epoch header.

Only ``tweet.fields`` are requested. Expanding author objects bills a second read per post
and adds nothing to sentiment analysis. Every query is free-form so the tool works for any
topic; the adapter only appends language and retweet filters when the caller has not.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record
from sentiment_prep.sources.spend_guard import SpendCapReachedError, SpendGuard

logger = get_logger(__name__)

PAGE_SIZE = 100
MIN_TOKENS = 5
MAX_RETRIES = 3
DEFAULT_QUERY_SUFFIX = "lang:en -is:retweet"

Json = dict[str, Any]


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
            except httpx.HTTPError:
                # Nothing was billed, so hand the reservation back before giving up.
                self._guard.release()
                raise
            if payload is None:
                self._guard.release()
                truncated_reason = "rate limited after retries"
                break

            posts = payload.get("data", [])
            self._guard.record(len(posts))
            records.extend(self._to_records(posts))
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
            reads_billed=self._guard.reads_this_fetch,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            truncated_reason=truncated_reason,
        )
        return Dataset(
            records=records, source_type="x", query=full_query, truncated_reason=truncated_reason
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
            response.raise_for_status()
            data: Json = response.json()
            return data
        return None

    @staticmethod
    def _backoff_seconds(response: httpx.Response, attempt: int) -> float:
        reset = response.headers.get("x-rate-limit-reset")
        if reset and reset.isdigit():
            return max(1.0, min(60.0, int(reset) - time.time()))
        return float(2**attempt)

    def _to_records(self, posts: list[Json]) -> list[Record]:
        records: list[Record] = []
        for post in posts:
            text = str(post.get("text", "")).strip()
            if len(text.split()) < MIN_TOKENS:
                continue
            if not self._allow_non_english and post.get("lang") not in (None, "en"):
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
        return records
