"""Turn an uploaded CSV into a ``Dataset``.

Requires a ``text`` column; ``label`` and ``id`` are optional. Column names are matched
case-insensitively so ``Text`` or ``TEXT`` also work.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable

from sentiment_prep.errors import ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import Dataset, Record

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_RECORDS = 5000

logger = get_logger(__name__)


class CsvUploadSource:
    """Parse CSV bytes held in memory."""

    name = "csv"

    def __init__(self, content: bytes) -> None:
        self._content = content

    def fetch(
        self,
        limit: int,
        query: str | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Dataset:
        """Read up to ``limit`` non-empty rows."""
        if not 1 <= limit <= MAX_UPLOAD_RECORDS:
            raise ValidationError(f"limit must be between 1 and {MAX_UPLOAD_RECORDS}")
        if len(self._content) > MAX_UPLOAD_BYTES:
            raise ValidationError("CSV exceeds the 4 MiB upload limit")
        try:
            text = self._content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV must be UTF-8 encoded") from exc
        if "\x00" in text:
            raise ValidationError("CSV must not contain NUL bytes")
        try:
            return self._parse(text, limit)
        except csv.Error as exc:
            raise ValidationError("Malformed CSV or field exceeds the CSV parser limit") from exc

    def _parse(self, text: str, limit: int) -> Dataset:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers:
            raise ValidationError("CSV has no header row")
        normalized = [name.strip().lower() for name in headers]
        if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValidationError("CSV headers must be non-empty and unique (ignoring case/space)")
        columns = dict(zip(normalized, headers, strict=True))
        if "text" not in columns:
            raise ValidationError(f"CSV needs a 'text' column; found {sorted(columns)}")
        records: list[Record] = []
        skipped = 0
        omitted = 0
        seen: dict[str, int] = {}
        for index, row in enumerate(reader):
            if index >= MAX_UPLOAD_RECORDS:
                raise ValidationError(f"CSV exceeds the {MAX_UPLOAD_RECORDS}-row limit")
            if None in row or any(value is None for value in row.values()):
                raise ValidationError(f"CSV row {reader.line_num} does not match the header width")
            value = row[columns["text"]].strip()
            if not value:
                skipped += 1
                continue
            record_id = str(row.get(columns.get("id", ""), "") or f"csv-{index}")
            if record_id in seen:
                raise ValidationError(
                    f"duplicate record id {record_id!r} at CSV row {reader.line_num}; "
                    f"first used at CSV row {seen[record_id]}"
                )
            seen[record_id] = reader.line_num
            if len(records) >= limit:
                omitted += 1
                continue
            records.append(
                Record(
                    id=record_id,
                    text=value,
                    label=(label := _csv_label(row, columns)),
                    label_source="source" if label is not None else None,
                    source_type="csv",
                )
            )
        logger.info("csv_parsed", returned=len(records), skipped_empty=skipped)
        return Dataset(
            records=records,
            source_type="csv",
            filtered_out={"empty_text": skipped} if skipped else {},
            truncated_reason=f"requested limit omitted {omitted} records" if omitted else None,
        )


def _csv_label(row: dict[str, str], columns: dict[str, str]) -> str | None:
    """Lower-cased label from the ``label`` column, or ``None`` when absent or blank."""
    if "label" not in columns:
        return None
    value = (row.get(columns["label"]) or "").strip().lower()
    return value or None
