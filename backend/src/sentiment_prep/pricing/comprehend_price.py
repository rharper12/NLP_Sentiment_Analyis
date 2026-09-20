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
import math
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from sentiment_prep.budget import can_start
from sentiment_prep.history import services as history
from sentiment_prep.logging_config import get_logger

if TYPE_CHECKING:
    from mypy_boto3_pricing.client import PricingClient

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

    model_config = ConfigDict(revalidate_instances="always")
    price_per_unit: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    unit: Annotated[str, Field(strict=True, pattern=r"^Units?$")]
    sku: Annotated[str, Field(strict=True, min_length=1)]
    region: Annotated[str, Field(strict=True, min_length=1)]
    fetched_at: AwareDatetime
    status: PriceStatus

    @field_validator("price_per_unit")
    @classmethod
    def usable_numeric_price(cls, value: Decimal) -> Decimal:
        """Reject values that cannot survive the API's numeric representation."""
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError("Price is not a representable positive number")
        return value


class PriceDimension(BaseModel):
    """Only USD per-character-unit dimensions can price sentiment requests."""

    unit: str
    begin_range: str = Field(alias="beginRange")
    price_per_unit: dict[str, Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]] = Field(
        alias="pricePerUnit"
    )


class PriceTerm(BaseModel):
    """On-demand dimensions, including volume tiers."""

    price_dimensions: dict[str, PriceDimension] = Field(alias="priceDimensions")


class Product(BaseModel):
    """Product identity and the attributes used to select standard sentiment."""

    model_config = ConfigDict(strict=True)
    sku: Annotated[str, Field(min_length=1)]
    attributes: dict[str, str]


class PriceProduct(BaseModel):
    """The consumed portion of one Price List product."""

    product: Product
    terms: dict[str, dict[str, PriceTerm]]


def _first_tier_price(product: PriceProduct, region: str) -> tuple[Decimal, str] | None:
    attributes = product.product.attributes
    usage = attributes.get("usagetype", "").lower()
    blob = " ".join(attributes.values()).lower()
    if (
        not SENTIMENT_USAGETYPE.search(usage)
        or any(word in blob for word in EXCLUDED_TERMS)
        or attributes.get("regionCode") != region
        or attributes.get("servicecode") != SERVICE_CODE
    ):
        return None
    prices = [
        (dimension.price_per_unit["USD"], dimension.unit)
        for term in product.terms.get("OnDemand", {}).values()
        for dimension in term.price_dimensions.values()
        if dimension.begin_range in ("0", "0.0")
        and dimension.unit in ("Unit", "Units")
        and set(dimension.price_per_unit) == {"USD"}
        and dimension.price_per_unit["USD"] > 0
    ]
    # Conflicting first-tier rates cannot safely be resolved by guessing the cheapest.
    return prices[0] if prices and len(set(prices)) == 1 else None


def fetch_rate(client: PricingClient, region: str) -> PriceQuote | None:
    """Query the Price List API once. Returns ``None`` when no sentiment SKU is found."""
    paginator = client.get_paginator("get_products")
    pages = paginator.paginate(
        ServiceCode=SERVICE_CODE,
        Filters=[{"Type": "TERM_MATCH", "Field": "regionCode", "Value": region}],
        FormatVersion="aws_v1",
    )
    iterator = iter(pages)
    # Bound both latency and pagination even when a catalogue has no matching SKU.
    for _ in range(3):
        if not can_start():
            break
        page = next(iterator, None)
        if page is None:
            break
        if not isinstance(page, dict) or not isinstance(page.get("PriceList"), list):
            raise TypeError("Malformed Price List page")
        for raw in page["PriceList"]:
            product = PriceProduct.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
            price = _first_tier_price(product, region)
            if price is None:
                continue
            quote = PriceQuote(
                price_per_unit=price[0],
                unit=price[1],
                sku=product.product.sku,
                region=region,
                fetched_at=dt.datetime.now(dt.UTC),
                status="live",
            )
            logger.info("comprehend_price_fetched", sku=quote.sku, price=str(quote.price_per_unit))
            return quote
    logger.warning("comprehend_price_not_found", region=region)
    return None


def current_rate(client: PricingClient | None, region: str, cache_hours: int) -> PriceQuote | None:
    """Cached rate if fresh; else a live lookup; else the stale cache; else ``None``."""
    cached = None
    now = dt.datetime.now(dt.UTC)
    try:
        stored = history.get_price_quote(SERVICE_CODE, region)
        if stored is not None:
            candidate = PriceQuote.model_validate(stored)
            age = now - candidate.fetched_at
            if candidate.region == region and age >= dt.timedelta(0):
                cached = candidate
                if age < dt.timedelta(hours=cache_hours):
                    return cached.model_copy(update={"status": "cached"})
    except Exception:  # noqa: BLE001 - every pricing cache failure is optional
        logger.warning("comprehend_price_cache_read_failed")
        cached = None

    if client is not None:
        try:
            quote = fetch_rate(client, region)
        except Exception:  # noqa: BLE001 - lookup and pagination are best effort
            logger.warning("comprehend_price_lookup_failed")
            quote = None
        if quote is not None:
            try:
                history.put_price_quote(quote)
            except Exception:  # noqa: BLE001 - keep a valid live rate if cache storage fails
                logger.warning("comprehend_price_cache_write_failed")
            return quote

    if cached:
        return cached.model_copy(update={"status": "stale"})
    return None
