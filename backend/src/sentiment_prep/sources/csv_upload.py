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
        try:
            text = self._content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("CSV must be UTF-8 encoded") from exc

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValidationError("CSV has no header row")
        columns = {name.strip().lower(): name for name in reader.fieldnames}
        if "text" not in columns:
            raise ValidationError(f"CSV needs a 'text' column; found {sorted(columns)}")

        records: list[Record] = []
        skipped = 0
        for index, row in enumerate(reader):
            value = (row.get(columns["text"]) or "").strip()
            if not value:
                skipped += 1
                continue
            records.append(
                Record(
                    id=str(row.get(columns.get("id", ""), "") or f"csv-{index}"),
                    text=value,
                    label=(label := _csv_label(row, columns)),
                    label_source="source" if label is not None else None,
                    source_type="csv",
                )
            )
            if len(records) >= limit:
                break

        logger.info("csv_parsed", returned=len(records), skipped_empty=skipped)
        return Dataset(
            records=records,
            source_type="csv",
            filtered_out={"empty_text": skipped} if skipped else {},
        )


def _csv_label(row: dict[str, str], columns: dict[str, str]) -> str | None:
    """Lower-cased label from the ``label`` column, or ``None`` when absent or blank."""
    if "label" not in columns:
        return None
    value = (row.get(columns["label"]) or "").strip().lower()
    return value or None
