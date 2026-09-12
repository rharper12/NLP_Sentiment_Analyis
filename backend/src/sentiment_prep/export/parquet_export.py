"""Parquet export of the joined rows, and CSV-to-Parquet conversion for checkpoints.

Parquet is what Task 2 should load: typed columns, compressed, and pandas reads it in one call.
CSV stays the checkpoint format because it is readable by anything, including a person.
"""

from __future__ import annotations

import io

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from sentiment_prep.export.rows import bundle_rows
from sentiment_prep.models import DatasetBundle


def to_parquet(bundle: DatasetBundle) -> bytes:
    """Serialise the bundle's rows to Parquet."""
    return _write(pa.Table.from_pylist(bundle_rows(bundle)))


def csv_to_parquet(csv_bytes: bytes) -> bytes:
    """Convert a CSV produced by ``csv_export`` (UTF-8 with BOM) to Parquet."""
    table = pacsv.read_csv(io.BytesIO(csv_bytes.removeprefix(b"\xef\xbb\xbf")))
    return _write(table)


def _write(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue()
