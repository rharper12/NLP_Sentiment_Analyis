"""Stage snapshots with explicit freshness and write-failure status.

Collected, processed and labelled CSV snapshots supplement the required working journal.
Snapshot failures are reported without discarding committed work. Files use local storage or
S3; the S3 adapter uses ``upload_fileobj`` with a configured multipart threshold.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import CheckpointStage, CheckpointState, DatasetBundle

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


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
    revision: str | None = None
    status: Literal["current", "stale", "failed"] = "stale"


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
        tmp: Path | None = None
        try:
            with NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as temporary:
                tmp = Path(temporary.name)
                temporary.write(data)
            tmp.replace(path)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
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


def invalidate_checkpoints(bundle: DatasetBundle, *stages: Stage) -> None:
    """Mark changed stages and their downstream snapshots stale, retaining previous files."""
    affected = {stage for upstream in stages for stage in STAGES[STAGES.index(upstream) :]}
    for key, state in bundle.checkpoint_status.items():
        if key.split(":")[0] in affected:
            state.status = "stale"


def checkpoint_warnings(bundle: DatasetBundle) -> list[str]:
    """Public-safe status only: never include paths, provider messages or credentials."""
    warnings = []
    for stage in STAGES:
        state = bundle.checkpoint_status.get(f"{stage}:csv")
        if state and state.status == "failed":
            warnings.append(
                f"The {stage} checkpoint could not be updated. "
                "Any previous snapshot may be outdated; download a fresh export."
            )
        elif state and state.status == "stale":
            action = (
                "rerun preprocessing before regenerating downstream checkpoints."
                if stage == "processed"
                else "download a fresh export."
            )
            warnings.append(f"The {stage} checkpoint is outdated; {action}")
    return warnings


def checkpoint_info(info: CheckpointInfo, bundle: DatasetBundle) -> CheckpointInfo:
    """Join actual stored files with revision metadata; legacy files have unknown freshness."""
    state = bundle.checkpoint_status.get(f"{info.stage}:{info.format}", CheckpointState())
    return info.model_copy(update={"revision": state.revision, "status": state.status})


def checkpoint_bundle(store: CheckpointStore, bundle: DatasetBundle, stage: Stage) -> DatasetBundle:
    """Attempt an atomic CSV replacement, retaining old files and exposing failure status."""
    from sentiment_prep.export.csv_export import to_checkpoint_csv

    updated = bundle.model_copy(deep=True)
    key = f"{stage}:csv"
    previous = updated.checkpoint_status.get(key, CheckpointState())
    try:
        content = to_checkpoint_csv(bundle)
        revision = hashlib.sha256(content).hexdigest()
        converted = updated.checkpoint_status.get(f"{stage}:parquet")
        if converted and converted.revision != revision:
            converted.status = "stale"
        info = store.save(bundle.dataset_id, stage, "csv", content)
    except Exception:  # a snapshot failure must not discard already committed paid work
        logger.error("checkpoint_failed", stage=stage, exc_info=True)
        converted = updated.checkpoint_status.get(f"{stage}:parquet")
        if converted:
            converted.status = "stale"
        updated.checkpoint_status[key] = previous.model_copy(update={"status": "failed"})
        return updated
    updated.checkpoints[stage] = info.uri
    # Label writes preserve valid reviewer labels, but cannot refresh an old processed input.
    processed = updated.checkpoint_status.get("processed:csv")
    stale_input = stage == "labelled" and processed is not None and processed.status != "current"
    updated.checkpoint_status[key] = CheckpointState(
        revision=revision, status="stale" if stale_input else "current"
    )
    return updated
