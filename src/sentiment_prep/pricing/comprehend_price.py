"""Current Amazon Comprehend sentiment price for a region.

The rate is looked up with ``pricing:GetProducts`` (ServiceCode ``AmazonComprehend``, filtered by
``regionCode``) and cached for 24 hours in the history database so warm and cold Lambda starts
share one lookup a day. The Price List endpoint lives in a few regions only (``us-east-1``,
``eu-central-1``, ``ap-south-1``); the *product* region is a filter, not the endpoint.

There is deliberately no hard-coded fallback price. If the lookup fails and nothing has ever
been cached, the estimate is reported as unavailable and the UI tells the person to price the
job by hand on AWS's pricing page. A stale cache is served rather than nothing, flagged as such.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from sentiment_prep.history import services as history
from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)

SERVICE_CODE = "AmazonComprehend"
PRICING_ENDPOINT_REGION = "us-east-1"
PriceStatus = Literal["live", "cached", "stale", "unavailable"]


# Sentiment SKUs are named like "USE1-Sentiment-Units"; the anchored form avoids matching
# "TargetedSentiment" or a future "SentimentBatch" by accident.
SENTIMENT_USAGETYPE = re.compile(r"(^|-)sentiment(-|$)")
EXCLUDED_TERMS = ("targeted", "custom", "async", "batch job")


class PriceQuote(BaseModel):
    """One resolved rate. ``status`` says how fresh it is.

    ``price_per_unit`` is a ``Decimal`` so money arithmetic is exact; the API serialises it to a
    float only in the response model.
    """

    price_per_unit: Decimal
    unit: str
    sku: str
    region: str
    fetched_at: dt.datetime
    status: PriceStatus


def _matches_sentiment(attributes: dict[str, Any]) -> bool:
    """True for the standard (not targeted, not custom) sentiment SKU.

    Prefers ``usagetype``, which is the field AWS uses to name the metered operation. Falls back
    to scanning every attribute value because attribute names differ between services and the
    Price List schema is not versioned. Either way the match is anchored and excludes targeted,
    custom, async and batch SKUs, and a miss degrades to "estimate unavailable" rather than a
    wrong number.
    """
    usagetype = str(attributes.get("usagetype", "")).lower()
    blob = " ".join(str(v) for v in attributes.values()).lower()
    if any(word in blob for word in EXCLUDED_TERMS):
        return False
    if usagetype:
        return bool(SENTIMENT_USAGETYPE.search(usagetype))
    return "sentiment" in blob


def _first_tier_price(product: dict[str, Any]) -> tuple[Decimal, str] | None:
    """USD price of the lowest usage tier among the OnDemand dimensions.

    Parsed straight into ``Decimal`` from the string AWS returns, never via ``float``, so the
    published price is preserved exactly.
    """
    best: tuple[Decimal, str] | None = None
    for term in product.get("terms", {}).get("OnDemand", {}).values():
        for dimension in term.get("priceDimensions", {}).values():
            price = dimension.get("pricePerUnit", {}).get("USD")
            begin = dimension.get("beginRange", "0")
            if price is None or begin not in ("0", "0.0"):
                continue
            value = Decimal(str(price))
            if value > 0 and (best is None or value < best[0]):
                best = (value, str(dimension.get("unit", "")))
    return best


def fetch_rate(client: Any, region: str) -> PriceQuote | None:
    """Query the Price List API once. Returns ``None`` when no sentiment SKU is found."""
    paginator = client.get_paginator("get_products")
    pages = paginator.paginate(
        ServiceCode=SERVICE_CODE,
        Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}],
        FormatVersion="aws_v1",
    )
    for page in pages:
        for raw in page.get("PriceList", []):
            product = json.loads(raw) if isinstance(raw, str) else raw
            attributes = product.get("product", {}).get("attributes", {})
            if not _matches_sentiment(attributes):
                continue
            price = _first_tier_price(product)
            if price is None:
                continue
            quote = PriceQuote(
                price_per_unit=price[0],
                unit=price[1],
                sku=str(product.get("product", {}).get("sku", "")),
                region=region,
                fetched_at=dt.datetime.now(dt.UTC),
                status="live",
            )
            logger.info("comprehend_price_fetched", sku=quote.sku, price=str(quote.price_per_unit))
            return quote
    logger.warning("comprehend_price_not_found", region=region)
    return None


def current_rate(client: Any | None, region: str, cache_hours: int) -> PriceQuote | None:
    """Cached rate if fresh; else a live lookup; else the stale cache; else ``None``."""
    cached = history.get_price_quote(SERVICE_CODE, region)
    now = dt.datetime.now(dt.UTC)
    if cached and now - cached.fetched_at < dt.timedelta(hours=cache_hours):
        return cached.model_copy(update={"status": "cached"})

    if client is not None:
        try:
            quote = fetch_rate(client, region)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to cache/unavailable
            logger.warning("comprehend_price_lookup_failed", error=str(exc))
            quote = None
        if quote is not None:
            history.put_price_quote(quote)
            return quote

    if cached:
        logger.warning("comprehend_price_stale", fetched_at=cached.fetched_at.isoformat())
        return cached.model_copy(update={"status": "stale"})
    return None
