"""Portable output names, independent of dataset identity and paid-job resume keys."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Annotated

from pydantic import StringConstraints

if TYPE_CHECKING:
    from sentiment_prep.models import DatasetBundle

# Extensions belong to the exporter. Excluding dots and separators also prevents paths.
FileStem = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _-]*$", max_length=120)
]


def default_file_stem(
    query: str | None, source: str, zone: tzinfo = UTC, *, at: datetime | None = None
) -> str:
    """Use a topic and local wall time with its UTC offset, safe on common filesystems."""
    topic = query or {"csv": "csv-import", "huggingface": "sample-tweets"}.get(source, "x-posts")
    # A quoted positive phrase is usually the topic; filters and Boolean syntax are not names.
    topic = re.sub(r'-?\b\w+:(?:"[^"]*"|[^\s)]+)', " ", topic)
    phrase = re.search(r'(?<![-\w])"([^"]+)"', topic)
    topic = phrase[1] if phrase else re.sub(r"\b(?:OR|AND|NOT)\b|(?<!\S)-[\w#]+", " ", topic)
    ascii_topic = unicodedata.normalize("NFKD", topic).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_topic.lower()).strip("-")[:60].rstrip("-")
    local = (at or datetime.now(UTC)).astimezone(zone)
    offset = local.strftime("%z").replace("+", "plus")
    stamp = f"{local:%Y-%m-%d_%H-%M-%S}-UTC{offset}"
    return f"{slug or source}-{stamp}"


def bundle_file_stem(bundle: DatasetBundle) -> str:
    """Legacy bundles get a stable default without rewriting their saved data."""
    return bundle.file_stem or default_file_stem(
        bundle.original.query, bundle.original.source_type, at=bundle.original.fetched_at
    )
