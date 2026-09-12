"""Checkpoints: CSV snapshots written the moment data exists.

Three stages are checkpointed: ``collected`` (raw posts, possibly paid for), ``processed``
(after the pipeline), and ``labelled`` (after Comprehend and manual review, paid for). If the
process crashes after a paid step, the checkpoint is the receipt. Locally they live in a
gitignored folder; in AWS they go to the data bucket through ``upload_fileobj``, which boto3
splits into a multipart upload automatically above ``TransferConfig.multipart_threshold``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import CheckpointStage

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

    from sentiment_prep.models import DatasetBundle

logger = get_logger(__name__)

# Single definition lives with the other domain types; aliased here for readability.
Stage = CheckpointStage
STAGES: tuple[Stage, ...] = ("collected", "processed", "labelled")
Format = Literal["csv", "parquet"]
FORMATS: tuple[Format, ...] = ("csv", "parquet")


class CheckpointInfo(BaseModel):
    """One stored file."""

    stage: Stage
    format: Format
    uri: str
    bytes: int
    written_at: datetime


class CheckpointStore(Protocol):
    """Write and read stage snapshots for a dataset."""

    def save(self, dataset_id: str, stage: Stage, fmt: Format, data: bytes) -> CheckpointInfo: ...

    def read(self, dataset_id: str, stage: Stage, fmt: Format) -> bytes | None: ...

    def list(self, dataset_id: str) -> list[CheckpointInfo]: ...


def _name(stage: Stage, fmt: Format) -> str:
    return f"{stage}.{fmt}"


class LocalCheckpointStore:
    """Files under ``{root}/{dataset_id}/``. The root is gitignored."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, dataset_id: str, stage: Stage, fmt: Format, data: bytes) -> CheckpointInfo:
        folder = self._root / dataset_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / _name(stage, fmt)
        # Write to a temp name then rename so a crash mid-write never leaves a half file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        info = CheckpointInfo(
            stage=stage, format=fmt, uri=str(path), bytes=len(data), written_at=datetime.now(UTC)
        )
        logger.info("checkpoint_saved", stage=stage, format=fmt, bytes=len(data), uri=info.uri)
        return info

    def read(self, dataset_id: str, stage: Stage, fmt: Format) -> bytes | None:
        path = self._root / dataset_id / _name(stage, fmt)
        return path.read_bytes() if path.exists() else None

    def list(self, dataset_id: str) -> list[CheckpointInfo]:
        folder = self._root / dataset_id
        if not folder.exists():
            return []
        found: list[CheckpointInfo] = []
        for stage in STAGES:
            for fmt in FORMATS:
                path = folder / _name(stage, fmt)
                if path.exists():
                    stat = path.stat()
                    found.append(
                        CheckpointInfo(
                            stage=stage,
                            format=fmt,
                            uri=str(path),
                            bytes=stat.st_size,
                            written_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                        )
                    )
        return found


class S3CheckpointStore:
    """Objects at ``s3://{bucket}/{prefix}/{dataset_id}/{stage}.{fmt}``."""

    def __init__(self, bucket: str, prefix: str, client: S3Client) -> None:
        """Write stage snapshots to a bucket.

        Args:
        bucket: Destination bucket for stage snapshots.
        prefix: Key prefix; each dataset gets a folder beneath it.
        client: boto3 S3 client, shared and owned by the caller.
        """
        from boto3.s3.transfer import TransferConfig

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._client = client
        # 8 MB parts, multipart above 8 MB: boto3's defaults, made explicit so they are visible.
        self._transfer = TransferConfig(
            multipart_threshold=8 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024
        )

    def _key(self, dataset_id: str, stage: Stage, fmt: Format) -> str:
        return f"{self._prefix}/{dataset_id}/{_name(stage, fmt)}"

    def save(self, dataset_id: str, stage: Stage, fmt: Format, data: bytes) -> CheckpointInfo:
        import io

        key = self._key(dataset_id, stage, fmt)
        started = time.perf_counter()
        self._client.upload_fileobj(
            io.BytesIO(data),
            self._bucket,
            key,
            ExtraArgs={"ContentType": "text/csv" if fmt == "csv" else "application/octet-stream"},
            Config=self._transfer,
        )
        info = CheckpointInfo(
            stage=stage,
            format=fmt,
            uri=f"s3://{self._bucket}/{key}",
            bytes=len(data),
            written_at=datetime.now(UTC),
        )
        logger.info(
            "checkpoint_saved",
            stage=stage,
            format=fmt,
            bytes=len(data),
            uri=info.uri,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return info

    def read(self, dataset_id: str, stage: Stage, fmt: Format) -> bytes | None:
        try:
            body = self._client.get_object(
                Bucket=self._bucket, Key=self._key(dataset_id, stage, fmt)
            )["Body"]
        except self._client.exceptions.NoSuchKey:
            return None
        data: bytes = body.read()
        return data

    def list(self, dataset_id: str) -> list[CheckpointInfo]:
        response = self._client.list_objects_v2(
            Bucket=self._bucket, Prefix=f"{self._prefix}/{dataset_id}/"
        )
        found: list[CheckpointInfo] = []
        for obj in response.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            stage_name, _, fmt_name = name.partition(".")
            # Narrow the parsed strings before trusting them: anything else in this prefix is not
            # a checkpoint and is skipped rather than turned into a malformed record.
            stage = next((s for s in STAGES if s == stage_name), None)
            fmt = next((f for f in FORMATS if f == fmt_name), None)
            if stage and fmt:
                found.append(
                    CheckpointInfo(
                        stage=stage,
                        format=fmt,
                        uri=f"s3://{self._bucket}/{obj['Key']}",
                        bytes=obj["Size"],
                        written_at=obj["LastModified"],
                    )
                )
        return found


def checkpoint_bundle(store: CheckpointStore, bundle: DatasetBundle, stage: Stage) -> DatasetBundle:
    """Write the bundle's rows as the ``stage`` CSV and record the URI on the bundle.

    Failures are logged and swallowed: a checkpoint is a safety net, and losing it must not fail
    the request that just spent money.
    """
    from sentiment_prep.export.csv_export import to_csv

    try:
        info = store.save(bundle.dataset_id, stage, "csv", to_csv(bundle))
    # Broad by design: a checkpoint is a safety net. Losing one must not fail the request that
    # just spent money, which is the very request the checkpoint exists to protect.
    except Exception:
        logger.error("checkpoint_failed", stage=stage, exc_info=True)
        return bundle
    return bundle.model_copy(update={"checkpoints": {**bundle.checkpoints, stage: info.uri}})
