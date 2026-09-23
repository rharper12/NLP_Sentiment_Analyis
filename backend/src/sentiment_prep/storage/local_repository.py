"""Durable local working bundles beside checkpoints, with cross-process edit exclusion.

The journal stores each paid unit before the next begins. An OS-level file lock protects
read/modify/write across independent local workers. A killed worker releases its lock; as with
any provider without idempotency tokens, a response lost before commit can require rebilling.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel

from sentiment_prep.errors import ConflictError, NotFoundError, ValidationError
from sentiment_prep.models import DatasetBundle
from sentiment_prep.storage.repository import BUSY, BundleEdit

DATASET_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


class LocalDatasetFile(BaseModel):
    """Picker metadata; record contents are read only when a file is selected."""

    dataset_id: str
    filename: str
    modified_at: datetime
    bytes: int


class LocalDatasetPage(BaseModel):
    """A bounded page of local JSON files, newest modification first."""

    total: int
    items: list[LocalDatasetFile]


class LocalRepository:
    """Atomic JSON snapshots and nonblocking per-dataset filesystem locks."""

    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, dataset_id: str) -> Path:
        if not DATASET_ID.fullmatch(dataset_id):
            raise ValidationError("Invalid dataset id")
        return self._root / f"{dataset_id}.json"

    @contextmanager
    def _lock(self, dataset_id: str) -> Iterator[None]:
        with self._path(dataset_id).with_suffix(".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConflictError(BUSY) from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, bundle: DatasetBundle) -> None:
        bundle = DatasetBundle.model_validate(bundle.model_dump())
        path = self._path(bundle.dataset_id)
        with NamedTemporaryFile(dir=self._root, suffix=".tmp", delete=False) as temporary:
            tmp = Path(temporary.name)
            try:
                temporary.write(bundle.model_dump_json().encode())
                temporary.flush()
                os.fsync(temporary.fileno())
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)

    def save(self, bundle: DatasetBundle) -> None:
        """Create only; updates require an edit claim."""
        with self._lock(bundle.dataset_id):
            if self._path(bundle.dataset_id).exists():
                raise ConflictError("Dataset already exists; updates require an exclusive edit")
            self._write(bundle)

    def get(self, dataset_id: str) -> DatasetBundle:
        """Reload an isolated snapshot, revalidating record identity."""
        try:
            # Refuse links and special files even if a listed file was replaced before opening.
            descriptor = os.open(
                self._path(dataset_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValidationError("Saved dataset must be a regular JSON file")
                bundle = DatasetBundle.model_validate_json(handle.read())
        except FileNotFoundError as exc:
            raise NotFoundError(f"dataset {dataset_id} not found") from exc
        if bundle.dataset_id != dataset_id:
            raise ValidationError("Stored dataset id does not match its storage key")
        return bundle

    def list_files(self, offset: int, limit: int) -> LocalDatasetPage:
        """List metadata without deserializing every potentially large working bundle."""
        files = []
        for path in self._root.glob("*.json"):
            if not DATASET_ID.fullmatch(path.stem):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # Atomic replacement or removal can race a directory listing.
            if stat.S_ISREG(info.st_mode):
                files.append(
                    LocalDatasetFile(
                        dataset_id=path.stem,
                        filename=path.name,
                        bytes=info.st_size,
                        modified_at=datetime.fromtimestamp(info.st_mtime, UTC),
                    )
                )
        files.sort(key=lambda item: (item.modified_at, item.filename), reverse=True)
        return LocalDatasetPage(total=len(files), items=files[offset : offset + limit])

    @contextmanager
    def edit(self, dataset_id: str) -> Iterator[BundleEdit]:
        """Claim before loading the snapshot or starting paid work."""
        with self._lock(dataset_id):
            editor = BundleEdit(self.get(dataset_id), self._write)
            try:
                yield editor
            finally:
                editor.close()
