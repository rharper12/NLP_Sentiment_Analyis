"""Bound record pages by their serialized Lambda payload, including JSON escaping."""

import json
from collections.abc import Iterable

from pydantic import BaseModel

from sentiment_prep.errors import ValidationError

# Leave 2 MiB of Lambda's 6 MiB response allowance for the envelope and page metadata.
PAGE_BYTES = 4 * 1024 * 1024


def bounded_page[T: BaseModel](rows: Iterable[T]) -> list[T]:
    """Return a complete prefix; callers continue at offset + len(items)."""
    items: list[T] = []
    size = 0
    for row in rows:
        # Mangum puts the JSON body inside another JSON string. Count that outer escaping
        # and ASCII-escaped Unicode too, rather than just UTF-8 text or the row count.
        row_size = len(json.dumps(row.model_dump_json())) + 1
        if size + row_size > PAGE_BYTES:
            if not items:
                raise ValidationError("This record is too large to display. Download an export.")
            break
        items.append(row)
        size += row_size
    return items
