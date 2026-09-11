"""Build the services the routes need from settings.

Everything AWS-facing is created lazily so a local developer without credentials can still load
a Hugging Face dataset and run preprocessing. Tests monkeypatch these functions to inject fakes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from sentiment_prep.config import Settings, get_settings
from sentiment_prep.errors import ConfigurationError
from sentiment_prep.history.services import DbLedger
from sentiment_prep.logging_config import get_logger
from sentiment_prep.sources.huggingface import HuggingFaceSource
from sentiment_prep.sources.spend_guard import SpendGuard, SpendLedger
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import (
    CheckpointStore,
    LocalCheckpointStore,
    S3CheckpointStore,
)
from sentiment_prep.storage.repository import BundleRepository, InMemoryRepository, S3Repository
from sentiment_prep.storage.s3_store import S3Store

if TYPE_CHECKING:
    from sentiment_prep.pricing.comprehend_price import PriceQuote

logger = get_logger(__name__)


def _boto_client(service: str, settings: Settings, region: str | None = None) -> Any:
    import boto3

    return boto3.client(service, region_name=region or settings.aws_region)


@lru_cache(maxsize=1)
def get_repository() -> BundleRepository:
    """In Lambda, bundles must survive across invocations, so S3 is mandatory there."""
    settings = get_settings()
    if settings.runtime == "lambda":
        if not settings.data_bucket:
            raise ConfigurationError("DATA_BUCKET is required when RUNTIME=lambda")
        return S3Repository(settings.data_bucket, _boto_client("s3", settings))
    logger.info("repository_in_memory")
    return InMemoryRepository()


@lru_cache(maxsize=1)
def get_checkpoint_store() -> CheckpointStore:
    """S3 when a bucket is configured, else a gitignored local folder."""
    settings = get_settings()
    if settings.data_bucket:
        return S3CheckpointStore(
            settings.data_bucket, settings.checkpoint_prefix, _boto_client("s3", settings)
        )
    logger.info("checkpoints_local", directory=settings.checkpoint_dir)
    return LocalCheckpointStore(settings.checkpoint_dir)


def checkpoint_location() -> Literal["local", "s3"]:
    """Where snapshots go, for the UI (diagnostics only)."""
    return "s3" if get_settings().data_bucket else "local"


def get_spend_ledger(query: str = "") -> SpendLedger:
    """Daily X read counter, append-only in the history database."""
    return DbLedger(get_settings().x_cost_per_read_usd, query=query)


def get_x_source(query: str = "") -> XSearchSource:
    """Fresh guard per request so the per-fetch cap starts at zero each time."""
    settings = get_settings()
    token = settings.resolve_x_bearer_token()
    if not token:
        raise ConfigurationError(
            "No X bearer token configured. Set X_BEARER_TOKEN or X_BEARER_TOKEN_SSM_PATH."
        )
    guard = SpendGuard(
        ledger=get_spend_ledger(query),
        max_per_fetch=settings.x_max_reads_per_fetch,
        max_per_day=settings.x_max_reads_per_day,
        cost_per_read_usd=settings.x_cost_per_read_usd,
    )
    return XSearchSource(bearer_token=token, guard=guard, base_url=settings.x_api_base_url)


def get_hf_source() -> HuggingFaceSource:
    """Configured dataset from settings."""
    s = get_settings()
    return HuggingFaceSource(
        dataset=s.hf_dataset,
        config=s.hf_config,
        split=s.hf_split,
        text_column=s.hf_text_column,
        label_column=s.hf_label_column,
        base_url=s.hf_api_base_url,
    )


def get_s3_store() -> S3Store:
    """User-facing save target."""
    settings = get_settings()
    if not settings.data_bucket:
        raise ConfigurationError("DATA_BUCKET is not configured; saving to S3 is unavailable")
    return S3Store(settings.data_bucket, settings.dataset_prefix, _boto_client("s3", settings))


def get_comprehend_rate() -> PriceQuote | None:
    """Current Comprehend sentiment rate from the Price List API (cached 24 h), or ``None``.

    ``None`` means the UI shows "estimate unavailable" rather than a guessed figure.
    """
    from sentiment_prep.pricing.comprehend_price import PRICING_ENDPOINT_REGION, current_rate

    settings = get_settings()
    client = None
    if settings.pricing_enabled:
        try:
            client = _boto_client("pricing", settings, region=PRICING_ENDPOINT_REGION)
        except Exception as exc:  # noqa: BLE001 - no credentials is a normal local state
            logger.warning("pricing_client_unavailable", error=str(exc))
    return current_rate(client, settings.aws_region, settings.pricing_cache_hours)


def get_comprehend_client() -> Any | None:
    """``None`` disables the sentiment comparison and labelling rather than failing requests."""
    settings = get_settings()
    return _boto_client("comprehend", settings) if settings.comprehend_enabled else None


def get_bedrock_client() -> Any | None:
    """Shared by the embedder and the explainer."""
    settings = get_settings()
    return _boto_client("bedrock-runtime", settings) if settings.bedrock_enabled else None
