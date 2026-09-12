"""Where in-flight datasets live between API calls.

Lambda is stateless across invocations, so the in-memory repository is only correct for local
development. In Lambda the S3 repository is used; every bundle round-trips through JSON.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import TYPE_CHECKING, Protocol

from sentiment_prep.errors import NotFoundError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = get_logger(__name__)


class BundleRepository(Protocol):
    """Save and load a ``DatasetBundle`` by id."""

    def save(self, bundle: DatasetBundle) -> None: ...

    def get(self, dataset_id: str) -> DatasetBundle: ...


class InMemoryRepository:
    """Bounded LRU cache of bundles; lost when the process exits.

    Bounded because a long-lived local or container process would otherwise hold every dataset
    ever loaded. Evicting the least recently used bundle is safe: the checkpoints on disk or in S3
    are the durable copy, and the UI always works from the most recent dataset.
    """

    def __init__(self, max_datasets: int = 20) -> None:
        self._bundles: OrderedDict[str, DatasetBundle] = OrderedDict()
        self._max = max_datasets

    def save(self, bundle: DatasetBundle) -> None:
        self._bundles[bundle.dataset_id] = bundle
        self._bundles.move_to_end(bundle.dataset_id)
        while len(self._bundles) > self._max:
            evicted, _ = self._bundles.popitem(last=False)
            logger.info("bundle_evicted", dataset_id=evicted, kept=len(self._bundles))

    def get(self, dataset_id: str) -> DatasetBundle:
        try:
            self._bundles.move_to_end(dataset_id)
            return self._bundles[dataset_id]
        except KeyError as exc:
            raise NotFoundError(f"dataset {dataset_id} not found") from exc


class S3Repository:
    """Stores each bundle at ``s3://{bucket}/_work/{dataset_id}.json``."""

    def __init__(self, bucket: str, client: S3Client) -> None:
        """Store working bundles as JSON objects in S3.

        Args:
        bucket: Data bucket. Bundles live under the ``_work/`` prefix.
        client: boto3 S3 client, shared and owned by the caller.
        """
        self._bucket = bucket
        self._client = client

    def _key(self, dataset_id: str) -> str:
        return f"_work/{dataset_id}.json"

    def save(self, bundle: DatasetBundle) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(bundle.dataset_id),
            Body=bundle.model_dump_json().encode(),
            ContentType="application/json",
        )
        logger.info("bundle_saved_to_s3", dataset_id=bundle.dataset_id)

    def get(self, dataset_id: str) -> DatasetBundle:
        try:
            body = self._client.get_object(Bucket=self._bucket, Key=self._key(dataset_id))["Body"]
        except self._client.exceptions.NoSuchKey as exc:
            raise NotFoundError(f"dataset {dataset_id} not found") from exc
        return DatasetBundle.model_validate(json.loads(body.read()))
