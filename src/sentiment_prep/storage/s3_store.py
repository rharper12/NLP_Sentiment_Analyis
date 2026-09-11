"""The user-facing "Save to S3" action.

Writes a timestamped folder containing Parquet data (for Task 2 modelling), the impact report,
and a manifest describing provenance. Separate from the working repository so a user's saved
outputs are never mixed with transient state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sentiment_prep import __version__
from sentiment_prep.export.parquet_export import to_parquet
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle

logger = get_logger(__name__)


class S3Store:
    """Save a bundle under ``s3://{bucket}/{prefix}/{dataset_id}/{timestamp}/``."""

    def __init__(self, bucket: str, prefix: str, client: Any) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = client

    def save(self, bundle: DatasetBundle) -> str:
        """Write the three files and return the folder URI."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        folder = f"{self._prefix}/{bundle.dataset_id}/{stamp}"

        self._put(f"{folder}/dataset.parquet", to_parquet(bundle), "application/octet-stream")

        report = bundle.report.model_dump_json(indent=2) if bundle.report else "{}"
        self._put(f"{folder}/impact.json", report.encode(), "application/json")

        manifest = {
            "dataset_id": bundle.dataset_id,
            "source_type": bundle.original.source_type,
            "query": bundle.original.query,
            "fetched_at": bundle.original.fetched_at.isoformat(),
            "saved_at": stamp,
            "record_count_original": len(bundle.original.records),
            "record_count_processed": len(bundle.processed.records) if bundle.processed else None,
            "labelled_records": sum(1 for r in bundle.original.records if r.label),
            "applied_steps": bundle.applied_steps,
            "app_version": __version__,
        }
        self._put(
            f"{folder}/manifest.json", json.dumps(manifest, indent=2).encode(), "application/json"
        )

        uri = f"s3://{self._bucket}/{folder}/"
        logger.info("dataset_saved", uri=uri, records=manifest["record_count_original"])
        return uri

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)
