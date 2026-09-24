"""Shared fixtures. AWS is never contacted: S3 uses moto, Comprehend uses fakes."""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from moto import mock_aws

from sentiment_prep.config import Settings, get_settings
from sentiment_prep.models import Dataset, Record


def pytest_configure(config):
    """Keep developer credentials and service settings out of the offline suite."""
    environment = pytest.MonkeyPatch()
    config.add_cleanup(environment.undo)
    environment.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        environment.delenv(name.upper(), raising=False)
    for name, value in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_EC2_METADATA_DISABLED": "true",
        "COMPREHEND_ENABLED": "false",
        "PRICING_ENABLED": "false",
    }.items():
        environment.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def database_ready(tmp_path_factory):
    """Separate each test session's history and checkpoints, including concurrent runs."""
    root = tmp_path_factory.mktemp("sentiment-prep")
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("DATABASE_URL", f"sqlite:///{root / 'history.sqlite3'}")
        environment.setenv("CHECKPOINT_DIR", str(root / "checkpoints"))
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()


SAMPLE_TEXTS = [
    "I absolutely LOVED this movie!!! Best film of the year :)",
    "Not good. Not good at all. @someone check https://example.com/review",
    "The cats were running around the houses, it was fine I guess.",
    "",
    "   ",
    "Terrible service, never coming back. #disappointed",
    "It's okay, nothing special but nothing wrong either.",
    "Wow just wow. Speechless. Ten out of ten.",
]


def make_dataset(texts: list[str] | None = None) -> Dataset:
    texts = texts if texts is not None else SAMPLE_TEXTS
    return Dataset(
        records=[Record(id=f"r{i}", text=t, source_type="csv") for i, t in enumerate(texts)],
        source_type="csv",
    )


@pytest.fixture
def dataset() -> Dataset:
    return make_dataset()


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client, "test-bucket"


class FakeComprehend:
    """Deterministic labels: text containing 'love' or 'ten' is POSITIVE, 'not'/'terrible' NEGATIVE."""

    def __init__(self) -> None:
        self.calls = 0

    def batch_detect_sentiment(self, TextList: list[str], LanguageCode: str) -> dict[str, Any]:
        self.calls += 1
        results = []
        for index, text in enumerate(TextList):
            lowered = text.lower()
            if "love" in lowered or "ten" in lowered:
                label = "POSITIVE"
            elif "not" in lowered or "terrible" in lowered:
                label = "NEGATIVE"
            else:
                label = "NEUTRAL"
            scores = {"Positive": 0.05, "Negative": 0.05, "Neutral": 0.05, "Mixed": 0.05}
            scores[label.capitalize()] = 0.85
            results.append({"Index": index, "Sentiment": label, "SentimentScore": scores})
        return {"ResultList": results, "ErrorList": []}


class FakePricing:
    """Price List API shaped like the real one: one sentiment SKU, one targeted-sentiment SKU."""

    def __init__(self, price: float = 0.0001, fail: bool = False) -> None:
        self.price = price
        self.fail = fail
        self.calls = 0

    def get_paginator(self, name: str) -> FakePricing:
        return self

    def paginate(self, **kwargs: Any):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no credentials")
        assert kwargs["ServiceCode"] == "AmazonComprehend"

        def product(sku: str, usagetype: str, price: float) -> str:
            return json.dumps(
                {
                    "product": {
                        "sku": sku,
                        "attributes": {
                            "usagetype": usagetype,
                            "regionCode": "us-east-1",
                            "servicecode": "AmazonComprehend",
                        },
                    },
                    "terms": {
                        "OnDemand": {
                            f"{sku}.T": {
                                "priceDimensions": {
                                    f"{sku}.T.D": {
                                        "unit": "Unit",
                                        "beginRange": "0",
                                        "endRange": "10000000",
                                        "pricePerUnit": {"USD": f"{price:.10f}"},
                                    }
                                }
                            }
                        }
                    },
                }
            )

        yield {
            "PriceList": [
                product("TARGET", "USE1-TargetedSentiment-Units", 0.0002),
                product("SENT", "USE1-Sentiment-Units", self.price),
            ]
        }
