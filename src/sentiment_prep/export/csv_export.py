"""CSV export of the joined original/processed rows."""

from __future__ import annotations

import csv
import io

from sentiment_prep.export.rows import ROW_COLUMNS, bundle_rows
from sentiment_prep.models import DatasetBundle


def to_csv(bundle: DatasetBundle) -> bytes:
    """UTF-8 CSV with a BOM so Excel opens it with the right encoding."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ROW_COLUMNS)
    writer.writeheader()
    writer.writerows(bundle_rows(bundle))
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
