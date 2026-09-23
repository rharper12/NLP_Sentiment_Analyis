"""Names are portable, timezone-aware, and independent of the full X query syntax."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import TypeAdapter

from sentiment_prep.filenames import FileStem, default_file_stem


@pytest.mark.parametrize(
    "query,topic",
    [
        ('("iPhone Duo" OR #iPhoneDuo) lang:en -is:retweet', "iphone-duo"),
        ("coffee OR tea -is:reply", "coffee-tea"),
        ("from:NASA has:images", "x"),
        ('"Café / phones" lang:en', "cafe-phones"),
        ('"../../unsafe\\topic"', "unsafe-topic"),
        ("z" * 200, "z" * 60),
    ],
)
def test_topic_names_are_portable(query, topic):
    name = default_file_stem(
        query, "x", ZoneInfo("America/Chicago"), at=datetime(2026, 9, 23, 17, 15, 30, tzinfo=UTC)
    )
    assert name == f"{topic}-2026-09-23_12-15-30-UTC-0500"
    assert TypeAdapter(FileStem).validate_python(name) == name


def test_dates_follow_local_midnight_and_daylight_saving():
    zone = ZoneInfo("America/Chicago")
    assert "2026-09-22_23-30-00-UTC-0500" in default_file_stem(
        "phone", "x", zone, at=datetime(2026, 9, 23, 4, 30, tzinfo=UTC)
    )
    assert "2026-01-22_22-30-00-UTC-0600" in default_file_stem(
        "phone", "x", zone, at=datetime(2026, 1, 23, 4, 30, tzinfo=UTC)
    )
    assert "UTCplus0530" in default_file_stem("phone", "x", ZoneInfo("Asia/Kolkata"))


@pytest.mark.parametrize("source,prefix", [("csv", "csv-import"), ("huggingface", "sample-tweets")])
def test_sources_without_queries_have_clear_defaults(source, prefix):
    assert default_file_stem(None, source).startswith(prefix + "-")
