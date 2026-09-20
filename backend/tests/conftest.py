"""Shared fixtures. AWS is never contacted: S3 uses moto, Comprehend/Bedrock use fakes."""

from __future__ import annotations

import json
import os
from io import BytesIO
from typing import Any

import boto3
import pytest
from moto import mock_aws

from sentiment_prep.models import Dataset, Record


@pytest.fixture(scope="session", autouse=True)
def database_ready():
    """Fresh SQLite file per test session so history assertions are deterministic."""
    import pathlib

    from sentiment_prep.config import get_settings

    pathlib.Path("/tmp/sentiment_prep_test.sqlite3").unlink(missing_ok=True)
    get_settings.cache_clear()


os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/sentiment_prep_test.sqlite3")
os.environ.setdefault("COMPREHEND_ENABLED", "false")
os.environ.setdefault("BEDROCK_ENABLED", "false")
os.environ.setdefault("CHECKPOINT_DIR", "/tmp/sentiment_prep_test_checkpoints")
os.environ.setdefault("PRICING_ENABLED", "false")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


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


class FakeBedrock:
    """Embeds by character histogram; explains with a fixed string."""

    def invoke_model(
        self, modelId: str, body: str, contentType: str, accept: str
    ) -> dict[str, Any]:
        text = json.loads(body)["inputText"]
        vector = [0.0] * json.loads(body)["dimensions"]
        for ch in text.lower():
            if "a" <= ch <= "z":
                vector[ord(ch) - 97] += 1.0
        return {"body": BytesIO(json.dumps({"embedding": vector}).encode())}

    def converse(
        self, modelId: str, messages: list[dict[str, Any]], inferenceConfig: dict[str, Any]
    ) -> dict[str, Any]:
        self.last_prompt = messages[0]["content"][0]["text"]
        return {
            "output": {"message": {"content": [{"text": "Vocabulary shrank; negations kept."}]}}
        }


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
