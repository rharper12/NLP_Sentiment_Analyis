"""Excel export: a ``data`` sheet plus an ``impact`` sheet with per-step statistics."""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from sentiment_prep.export.rows import ROW_COLUMNS, bundle_rows
from sentiment_prep.models import DatasetBundle

IMPACT_COLUMNS = [
    "step_name",
    "records_in",
    "records_out",
    "vocab_before",
    "vocab_after",
    "avg_tokens_before",
    "avg_tokens_after",
    "duration_ms",
]


def to_excel(bundle: DatasetBundle) -> bytes:
    """Build the workbook in memory."""
    workbook = Workbook()
    data = workbook.active
    data.title = "data"
    _write_sheet(data, ROW_COLUMNS, [[row[c] for c in ROW_COLUMNS] for row in bundle_rows(bundle)])

    impact = workbook.create_sheet("impact")
    steps = bundle.report.steps if bundle.report else []
    _write_sheet(impact, IMPACT_COLUMNS, [[getattr(s, c) for c in IMPACT_COLUMNS] for s in steps])
    if bundle.report:
        impact.append([])
        impact.append(
            [
                "sentiment_agreement",
                bundle.report.sentiment.agreement if bundle.report.sentiment else None,
            ]
        )
        impact.append(["embedding_drift", bundle.report.embedding_drift])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_sheet(sheet: Worksheet, columns: list[str], rows: list[list[object]]) -> None:
    """Write a header row and body, then size and freeze the header."""
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = 40 if "text" in column else 16
    sheet.freeze_panes = "A2"
