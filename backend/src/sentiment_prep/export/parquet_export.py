"""Parquet export of the joined rows, and CSV-to-Parquet conversion for checkpoints.

Parquet is what Task 2 should load: typed columns, compressed, and pandas reads it in one call.
CSV stays the checkpoint format because it is readable by anything, including a person.
"""

from __future__ import annotations

import io
import json

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from sentiment_prep.eligibility import counts, reviewed_records
from sentiment_prep.export.rows import ROW_COLUMNS, bundle_rows
from sentiment_prep.models import DatasetBundle

EXPORT_SCHEMA = pa.schema(
    [
        pa.field(
            name,
            pa.float64() if name in {"label_confidence", "comprehend_confidence"} else pa.string(),
        )
        for name in ROW_COLUMNS
    ]
)


def to_parquet(bundle: DatasetBundle) -> bytes:
    """Serialise the bundle's rows to Parquet."""
    return _write(pa.Table.from_pylist(bundle_rows(bundle), schema=EXPORT_SCHEMA))


def to_reviewed_parquet(bundle: DatasetBundle) -> bytes:
    """Export only fully reviewed sampled inclusions, with provenance in the same file."""
    summary = counts(bundle)
    records = {r.id: r for r in reviewed_records(bundle)}
    rows = []
    for row in bundle_rows(bundle):
        record = records.get(row["id"])
        if record is None:
            continue
        row.update(
            author_id=record.author_id,
            eligibility=record.eligibility,
            eligibility_reviewed=record.eligibility_reviewed,
            eligibility_reason=record.eligibility_reason,
            eligibility_note=record.eligibility_note,
            screening=record.screening.model_dump_json() if record.screening else None,
            eligibility_history=json.dumps(
                [event.model_dump(mode="json") for event in record.eligibility_history]
            ),
            sentiment_reviewed=record.sentiment_reviewed,
            sentiment_reviewed_at=record.sentiment_reviewed_at.isoformat()
            if record.sentiment_reviewed_at
            else None,
        )
        rows.append(row)
    fields = [
        pa.field(name, pa.string())
        for name in (
            "author_id",
            "eligibility",
            "eligibility_reason",
            "eligibility_note",
            "screening",
            "eligibility_history",
            "sentiment_reviewed_at",
        )
    ] + [pa.field("eligibility_reviewed", pa.bool_()), pa.field("sentiment_reviewed", pa.bool_())]
    policy = bundle.original.consumer_policy
    assert policy is not None  # counts() rejects legacy datasets before constructing rows.
    metadata = {
        "dataset_id": bundle.dataset_id,
        "query": bundle.original.query,
        "policy": policy.model_dump(mode="json"),
        "counts": summary.model_dump(mode="json"),
        "status": "complete" if summary.shortfall == 0 else "partial",
        "collection": bundle.collection.model_dump(
            mode="json", exclude={"next_token", "slices", "seen_ids"}
        )
        if bundle.collection
        else None,
        "scope": "Screened bounded sample; not a representative survey or verified-human dataset",
    }
    schema = pa.schema(
        list(EXPORT_SCHEMA) + fields,
        metadata={
            b"sentiment_prep": json.dumps(metadata).encode(),
        },
    )
    return _write(pa.Table.from_pylist(rows, schema=schema))


def csv_to_parquet(csv_bytes: bytes) -> bytes:
    """Convert a raw checkpoint CSV, preserving quoted strings and unquoted nulls."""
    table = pacsv.read_csv(
        io.BytesIO(csv_bytes.removeprefix(b"\xef\xbb\xbf")),
        convert_options=pacsv.ConvertOptions(
            column_types=EXPORT_SCHEMA,
            include_columns=ROW_COLUMNS,
            strings_can_be_null=True,
            quoted_strings_can_be_null=False,
            null_values=[""],
        ),
    )
    return _write(table)


def _write(table: pa.Table) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="snappy")
    return buffer.getvalue()
