"""The user-facing "Save to S3" action.

Writes a named folder containing Parquet data (for Task 2 modelling), the impact report,
and a manifest describing provenance. Separate from the working repository so a user's saved
outputs are never mixed with transient state.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sentiment_prep import __version__
from sentiment_prep.export.parquet_export import to_parquet
from sentiment_prep.filenames import bundle_file_stem
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle
from sentiment_prep.presentation import public_report

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = get_logger(__name__)


class S3Store:
    """Named exports beneath dataset/name/save-ID folders; repeat saves never overwrite."""

    def __init__(self, bucket: str, prefix: str, client: S3Client) -> None:
        """Write user-facing saves to a bucket.

        Args:
        bucket: Destination bucket for user-facing saves.
        prefix: Key prefix, with an isolated folder for every save beneath it.
        client: boto3 S3 client, shared and owned by the caller.
        """
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = client

    def download_link(
        self, content: bytes, media_type: str, filename: str, *, expires_in: int
    ) -> str:
        """Stage one immutable private export; the bucket expires this prefix after a day."""
        key = f"_downloads/{uuid.uuid4().hex}/{filename}"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType=media_type,
            ContentDisposition=f'attachment; filename="{filename}"',
            CacheControl="no-store",
        )
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_in
        )

    def save(
        self, bundle: DatasetBundle, *, diagnostics: bool = False, filename: str | None = None
    ) -> str:
        """Write the three files and return the folder URI."""
        stamp = datetime.now(UTC).isoformat()
        stem = filename or bundle_file_stem(bundle)
        # Separate saves remain distinct even when two requests use the same name and second.
        folder = f"{self._prefix}/{bundle.dataset_id}/{stem}/{uuid.uuid4().hex}"

        self._put(f"{folder}/{stem}.parquet", to_parquet(bundle), "application/octet-stream")

        report = (
            public_report(bundle.report, diagnostics=diagnostics).model_dump_json(
                indent=2,
                exclude={} if diagnostics else {"steps": {"__all__": {"duration_ms"}}},
            )
            if bundle.report
            else "{}"
        )
        self._put(f"{folder}/impact.json", report.encode(), "application/json")

        manifest = {
            "dataset_id": bundle.dataset_id,
            "source_type": bundle.original.source_type,
            "query": bundle.original.query,
            "fetched_at": bundle.original.fetched_at.isoformat(),
            "saved_at": stamp,
            "filename": f"{stem}.parquet",
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
