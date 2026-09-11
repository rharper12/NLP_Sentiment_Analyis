"""Where in-flight datasets live between API calls.

Lambda is stateless across invocations, so the in-memory repository is only correct for local
development. In Lambda the S3 repository is used; every bundle round-trips through JSON.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from sentiment_prep.errors import NotFoundError
from sentiment_prep.logging_config import get_logger
from sentiment_prep.models import DatasetBundle

logger = get_logger(__name__)


class BundleRepository(Protocol):
    """Save and load a ``DatasetBundle`` by id."""

    def save(self, bundle: DatasetBundle) -> None: ...

    def get(self, dataset_id: str) -> DatasetBundle: ...


class InMemoryRepository:
    """Dict-backed; lost when the process exits."""

    def __init__(self) -> None:
        self._bundles: dict[str, DatasetBundle] = {}

    def save(self, bundle: DatasetBundle) -> None:
        self._bundles[bundle.dataset_id] = bundle

    def get(self, dataset_id: str) -> DatasetBundle:
        try:
            return self._bundles[dataset_id]
        except KeyError as exc:
            raise NotFoundError(f"dataset {dataset_id} not found") from exc


class S3Repository:
    """Stores each bundle at ``s3://{bucket}/_work/{dataset_id}.json``."""

    def __init__(self, bucket: str, client: Any) -> None:
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
