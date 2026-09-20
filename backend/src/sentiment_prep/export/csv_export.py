"""Separate spreadsheet-facing CSV downloads from lossless raw checkpoints."""

from __future__ import annotations

import csv
import io
import unicodedata
from typing import Any, Literal

from sentiment_prep.export.rows import ROW_COLUMNS, bundle_rows
from sentiment_prep.models import DatasetBundle


def spreadsheet_text(value: str) -> str:
    """Neutralize formulas even after whitespace or invisible control/format characters."""
    visible = value
    while visible and (visible[0].isspace() or unicodedata.category(visible[0]) in {"Cc", "Cf"}):
        visible = visible[1:]
    return "'" + value if visible.startswith(("=", "+", "-", "@")) else value


def _write(rows: list[dict[str, Any]], *, quoting: Literal[0, 5]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ROW_COLUMNS, quoting=quoting)
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def to_csv(bundle: DatasetBundle) -> bytes:
    """Spreadsheet-safe UTF-8 CSV. Use Parquet or raw checkpoints for training data."""
    rows = [
        {
            key: spreadsheet_text(value) if isinstance(value, str) else value
            for key, value in row.items()
        }
        for row in bundle_rows(bundle)
    ]
    return _write(rows, quoting=csv.QUOTE_MINIMAL)


def to_checkpoint_csv(bundle: DatasetBundle) -> bytes:
    """Raw machine-readable CSV: unquoted empty means null; quoted empty means empty text."""
    return _write(bundle_rows(bundle), quoting=csv.QUOTE_NOTNULL)
