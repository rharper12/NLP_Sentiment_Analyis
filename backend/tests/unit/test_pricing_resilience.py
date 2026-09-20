"""Pricing is optional across its complete read, validation, lookup and write boundary."""

import datetime as dt
import json
from decimal import Decimal

import pytest

from sentiment_prep.history import services as history
from sentiment_prep.pricing.comprehend_price import PriceQuote, current_rate
from tests.conftest import FakePricing


def quote(**changes):
    return PriceQuote.model_construct(
        price_per_unit=Decimal("0.0001"),
        unit="Unit",
        sku="SENT",
        region="us-east-1",
        fetched_at=dt.datetime.now(dt.UTC),
        status="cached",
    ).model_copy(update=changes)


def fail(*args, **kwargs):
    raise RuntimeError("unavailable pricing storage")


def test_cache_read_failure_still_uses_live_quote(monkeypatch):
    monkeypatch.setattr(history, "get_price_quote", fail)
    monkeypatch.setattr(history, "put_price_quote", lambda quote: None)
    assert current_rate(FakePricing(), "us-east-1", 24).status == "live"


def test_cache_write_failure_preserves_live_quote(monkeypatch):
    monkeypatch.setattr(history, "get_price_quote", lambda *args: None)
    monkeypatch.setattr(history, "put_price_quote", fail)
    result = current_rate(FakePricing(), "us-east-1", 24)
    assert result.status == "live" and result.price_per_unit == Decimal("0.0001")


@pytest.mark.parametrize(
    "cached",
    [
        None,
        {},
        {"price_per_unit": "no"},
        *[
            quote(price_per_unit=bad)
            for bad in (
                Decimal("NaN"),
                Decimal("1e999"),
                Decimal("1e-999"),
                Decimal("Infinity"),
                Decimal("-Infinity"),
                Decimal("-1"),
                Decimal("0"),
                "bad",
            )
        ],
        quote(unit="Hours"),
        quote(fetched_at="not-a-date"),
        quote(fetched_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC).replace(tzinfo=None)),
        quote(fetched_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=1)),
        quote(region="elsewhere"),
        quote(price_per_unit=Decimal("NaN"), fetched_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC)),
    ],
)
def test_invalid_cache_is_never_used_when_lookup_fails(monkeypatch, cached):
    monkeypatch.setattr(history, "get_price_quote", lambda *args: cached)
    assert current_rate(FakePricing(fail=True), "us-east-1", 24) is None


def test_failed_lookup_retains_valid_stale_cache(monkeypatch):
    monkeypatch.setattr(
        history,
        "get_price_quote",
        lambda *args: quote(fetched_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC)),
    )
    result = current_rate(FakePricing(fail=True), "us-east-1", 24)
    assert result.status == "stale" and result.price_per_unit == Decimal("0.0001")


def product():
    return json.loads(next(FakePricing().paginate(ServiceCode="AmazonComprehend"))["PriceList"][1])


def dimension(payload):
    return next(iter(next(iter(payload["terms"]["OnDemand"].values()))["priceDimensions"].values()))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(product=None),
        lambda p: p.update(terms=[]),
        lambda p: p["product"].update(sku=None),
        lambda p: p["product"]["attributes"].update(usagetype="USE1-TargetedSentiment-Units"),
        lambda p: p["product"]["attributes"].pop("usagetype"),
        lambda p: p["product"]["attributes"].update(regionCode="other"),
        lambda p: dimension(p).update(unit="Hours"),
        lambda p: dimension(p).update(beginRange="1000"),
        lambda p: dimension(p).update(pricePerUnit={"EUR": "0.001"}),
        *[
            lambda p, value=v: dimension(p).update(pricePerUnit={"USD": value})
            for v in ("NaN", "Infinity", "-Infinity", "-1", "0", "bad", None, True)
        ],
    ],
)
def test_invalid_live_pricing_is_unavailable(monkeypatch, mutate):
    monkeypatch.setattr(history, "get_price_quote", lambda *args: None)
    payload = product()
    mutate(payload)

    class Provider(FakePricing):
        def paginate(self, **kwargs):
            yield {"PriceList": [json.dumps(payload)]}

    assert current_rate(Provider(), "us-east-1", 24) is None


def test_pagination_failure_is_optional(monkeypatch):
    monkeypatch.setattr(history, "get_price_quote", lambda *args: None)

    class Provider(FakePricing):
        def paginate(self, **kwargs):
            yield {"PriceList": []}
            raise RuntimeError("pagination failed")

    assert current_rate(Provider(), "us-east-1", 24) is None


def test_unrepresentable_cost_does_not_block_labeling():
    from sentiment_prep.config import Settings
    from sentiment_prep.labeling.service import estimate, label_with_comprehend
    from sentiment_prep.models import DatasetBundle
    from tests.conftest import FakeComprehend, make_dataset

    bundle = DatasetBundle(dataset_id="cost", original=make_dataset(["valid text"]))
    rate = PriceQuote.model_validate(quote(price_per_unit=Decimal("1e100")))
    estimated = estimate(bundle, Settings(_env_file=None), rate)
    assert estimated.estimated_cost_usd is None and estimated.price_status == "unavailable"
    assert estimated.cost_per_unit_usd is None
    _, progress = label_with_comprehend(
        bundle, FakeComprehend(), Settings(_env_file=None), 25, rate
    )
    assert progress.labelled_in_call == 1 and progress.cost_usd is None
