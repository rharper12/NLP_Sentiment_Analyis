"""Exclusive dataset edits, including the paid work that precedes a write.

S3 claims live in the bundle object and use conditional writes, so they work across Lambda
instances with existing GetObject/PutObject permissions. Claims never expire automatically:
a terminated worker leaves an explicit conflict, requiring operator recovery after checking
its last committed bundle. Guessing that paid work stopped would allow duplicate billing.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from botocore.exceptions import ClientError

from sentiment_prep.errors import ConflictError, NotFoundError, ValidationError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = get_logger(__name__)
BUSY = "This dataset is being edited by another request. Retry after that operation finishes."


def _validated(bundle: DatasetBundle) -> DatasetBundle:
    # model_copy intentionally skips validation; persistence must not.
    return DatasetBundle.model_validate(bundle.model_dump())


class BundleEdit:
    """A claimed snapshot. Only explicit saves become visible to other readers."""

    def __init__(self, bundle: DatasetBundle, commit: Callable[[DatasetBundle], None]) -> None:
        self._bundle = bundle.model_copy(deep=True)
        self._commit = commit
        self._closed = False

    @property
    def bundle(self) -> DatasetBundle:
        """An isolated snapshot; modifying it does not modify stored state."""
        return self._bundle.model_copy(deep=True)

    def save(self, bundle: DatasetBundle) -> None:
        """Persist a validated update while retaining the exclusive claim."""
        if self._closed:
            raise ConflictError("This dataset edit has already finished")
        if bundle.dataset_id != self._bundle.dataset_id:
            raise ValidationError("An edit cannot change the dataset id")
        validated = _validated(bundle)
        self._commit(validated)
        self._bundle = validated.model_copy(deep=True)

    def close(self) -> None:
        """Prevent stale edit handles from being used after release."""
        self._closed = True


class BundleRepository(Protocol):
    """Create/read bundles. Existing bundles may only be changed through ``edit``."""

    def save(self, bundle: DatasetBundle) -> None: ...

    def get(self, dataset_id: str) -> DatasetBundle: ...

    def edit(self, dataset_id: str) -> AbstractContextManager[BundleEdit]: ...


class InMemoryRepository:
    """Bounded process-local storage, rejecting overlapping edits like the S3 repository."""

    def __init__(self, max_datasets: int = 20) -> None:
        self._bundles: OrderedDict[str, DatasetBundle] = OrderedDict()
        self._max = max_datasets
        self._mutex = threading.RLock()
        self._active: set[str] = set()

    def save(self, bundle: DatasetBundle) -> None:
        bundle = _validated(bundle)
        with self._mutex:
            if bundle.dataset_id in self._bundles:
                raise ConflictError("Dataset already exists; updates require an exclusive edit")
            if len(self._bundles) >= self._max:
                evicted = next((key for key in self._bundles if key not in self._active), None)
                if evicted is None:
                    raise ConflictError(BUSY)
                del self._bundles[evicted]
                logger.info("bundle_evicted", dataset_id=evicted)
            self._bundles[bundle.dataset_id] = bundle

    def get(self, dataset_id: str) -> DatasetBundle:
        with self._mutex:
            try:
                self._bundles.move_to_end(dataset_id)
                return _validated(self._bundles[dataset_id])
            except KeyError as exc:
                raise NotFoundError(f"dataset {dataset_id} not found") from exc

    @contextmanager
    def edit(self, dataset_id: str) -> Iterator[BundleEdit]:
        with self._mutex:
            if dataset_id in self._active:
                raise ConflictError(BUSY)
            bundle = self.get(dataset_id)
            self._active.add(dataset_id)

        def commit(updated: DatasetBundle) -> None:
            with self._mutex:
                self._bundles[dataset_id] = updated.model_copy(deep=True)

        editor = BundleEdit(bundle, commit)
        try:
            yield editor
        finally:
            editor.close()
            with self._mutex:
                self._active.remove(dataset_id)


class S3Repository:
    """Conditional claims on ``_work/{dataset_id}.json``; no extra IAM actions required."""

    def __init__(self, bucket: str, client: S3Client) -> None:
        """Use the existing data bucket and shared S3 client."""
        self._bucket = bucket
        self._client = client

    def _key(self, dataset_id: str) -> str:
        return f"_work/{dataset_id}.json"

    def _read(self, dataset_id: str) -> tuple[DatasetBundle, str, str | None]:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(dataset_id))
        except self._client.exceptions.NoSuchKey as exc:
            raise NotFoundError(f"dataset {dataset_id} not found") from exc
        with response["Body"] as body:
            payload: dict[str, Any] = json.loads(body.read())
        bundle = DatasetBundle.model_validate(payload)
        if bundle.dataset_id != dataset_id:
            raise ValidationError("Stored dataset id does not match its storage key")
        claim = payload.get("_write_claim")
        return bundle, response["ETag"], str(claim) if claim else None

    def _put(self, bundle: DatasetBundle, claim: str | None, etag: str | None) -> str:
        payload = bundle.model_dump(mode="json")
        payload.update(_write_claim=claim, _revision=uuid.uuid4().hex)
        content = json.dumps(payload).encode()
        try:
            if etag is None:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._key(bundle.dataset_id),
                    Body=content,
                    ContentType="application/json",
                    IfNoneMatch="*",
                )
            else:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=self._key(bundle.dataset_id),
                    Body=content,
                    ContentType="application/json",
                    IfMatch=etag,
                )
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in {409, 412}:
                raise ConflictError(BUSY) from exc
            raise
        return response["ETag"]

    def save(self, bundle: DatasetBundle) -> None:
        self._put(_validated(bundle), None, None)

    def get(self, dataset_id: str) -> DatasetBundle:
        return self._read(dataset_id)[0]

    @contextmanager
    def edit(self, dataset_id: str) -> Iterator[BundleEdit]:
        bundle, etag, active = self._read(dataset_id)
        if active:
            raise ConflictError(BUSY)
        claim = uuid.uuid4().hex
        etag = self._put(bundle, claim, etag)  # Must succeed before any paid work starts.

        def commit(updated: DatasetBundle) -> None:
            nonlocal etag
            etag = self._put(updated, claim, etag)

        editor = BundleEdit(bundle, commit)
        try:
            yield editor
        finally:
            editor.close()
            # A failed conditional release leaves the claim intact rather than overwriting
            # newer state. The revision nonce prevents an unchanged edit from causing ABA.
            self._put(editor.bundle, None, etag)
