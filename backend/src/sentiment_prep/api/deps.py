"""Build the services the routes need from settings.

Clients are expensive to construct and safe to share, so HTTP and AWS clients are cached for the
life of the process and injected into the objects that use them; nothing here builds a client per
request. ``close_clients`` releases them on shutdown and is called from the app's lifespan handler.
Tests monkeypatch these functions to inject fakes.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Header

from sentiment_prep.aws import create_aws_session
from sentiment_prep.budget import AWS_CONFIG
from sentiment_prep.config import get_settings
from sentiment_prep.errors import ConfigurationError, ValidationError
from sentiment_prep.history.db import require_durable_spend_storage
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
from sentiment_prep.storage.local_repository import LocalRepository
from sentiment_prep.storage.repository import BundleRepository, S3Repository
from sentiment_prep.storage.s3_store import S3Store

if TYPE_CHECKING:
    from mypy_boto3_comprehend.client import ComprehendClient
    from mypy_boto3_pricing.client import PricingClient
    from mypy_boto3_s3.client import S3Client

    from sentiment_prep.pricing.comprehend_price import PriceQuote

logger = get_logger(__name__)


def browser_time_zone(
    name: Annotated[str, Header(alias="X-Time-Zone", max_length=80)] = "UTC",
) -> ZoneInfo:
    """Use the browser's IANA zone rather than a cloud server's local clock."""
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValidationError(
            "X-Time-Zone must be a valid IANA timezone, such as America/Chicago"
        ) from exc


# Bound each page request so it fits within the API's request budget.
HTTP_TIMEOUT = httpx.Timeout(3.0, connect=1.0, pool=1.0)


class Closeable(Protocol):
    """A client owned by this factory, closed only at real process/server teardown."""

    def close(self) -> None: ...


_open_clients: list[Closeable] = []


@lru_cache(maxsize=4)
def _http_client(base_url: str, bearer_token: str | None = None) -> httpx.Client:
    """One pooled client per base URL, reused for the life of the process.

    Args:
        base_url: Origin the client is bound to.
        bearer_token: Sent as an ``Authorization`` header when given.

    Returns:
        A shared ``httpx.Client``. Callers must not close it; see ``close_clients``.
    """
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    client = httpx.Client(base_url=base_url, headers=headers, timeout=HTTP_TIMEOUT)
    _open_clients.append(client)
    return client


AwsService = Literal["s3", "comprehend", "pricing", "ssm"]


@lru_cache(maxsize=1)
def boto_session() -> Any:
    """One boto3 session per process, built from whichever credentials are configured.

    Precedence is explicit keys, then a named profile, then boto3's own chain. A session is what
    carries a profile, so SSO credentials resolve and refresh through it; in Lambda nothing is
    configured and the chain finds the task role.
    """
    settings = get_settings()
    logger.info(
        "boto_session_created",
        source="static keys"
        if settings.has_static_keys
        else settings.aws_profile or "default chain",
    )
    return create_aws_session(settings)


@lru_cache(maxsize=8)
def _boto_client(service: AwsService, region: str) -> Any:
    """Cached boto3 client. Clients are thread-safe and meant to be reused, not rebuilt.

    Returns ``Any`` because ``boto3.client`` is overloaded on literal service names and one
    generic factory cannot satisfy every overload; the public getters below narrow the type, so
    callers still see a precise client. ``Settings`` is deliberately not a parameter: it is
    unhashable and would defeat the cache.
    """
    logger.debug("boto_client_created", service=service, region=region)
    client = boto_session().client(service, region_name=region, config=AWS_CONFIG)
    _open_clients.append(client)
    return client


def close_clients() -> None:
    """Close owned clients once and discard every object retaining them at real teardown."""
    get_repository.cache_clear()
    get_checkpoint_store.cache_clear()
    boto_session.cache_clear()
    owned = {id(c): c for c in _open_clients}
    _open_clients.clear()
    _http_client.cache_clear()
    _boto_client.cache_clear()
    for client in owned.values():
        try:
            client.close()
        except Exception:  # One broken close must not strand other owned clients.
            logger.warning("client_close_failed", exc_info=True)
    logger.info("clients_closed")


@lru_cache(maxsize=1)
def get_repository() -> BundleRepository:
    """In Lambda, bundles must survive across invocations, so S3 is mandatory there."""
    settings = get_settings()
    if settings.runtime == "lambda":
        if not settings.data_bucket:
            raise ConfigurationError("DATA_BUCKET is required when RUNTIME=lambda")
        return S3Repository(settings.data_bucket, _s3_client())
    return LocalRepository(Path(settings.checkpoint_dir) / "_work")


@lru_cache(maxsize=1)
def get_checkpoint_store() -> CheckpointStore:
    """S3 when a bucket is configured, else a gitignored local folder."""
    settings = get_settings()
    if settings.data_bucket:
        return S3CheckpointStore(settings.data_bucket, settings.checkpoint_prefix, _s3_client())
    logger.info("checkpoints_local", directory=settings.checkpoint_dir)
    return LocalCheckpointStore(settings.checkpoint_dir)


def checkpoint_location() -> Literal["local", "s3"]:
    """Where snapshots go, for the UI (diagnostics only)."""
    return "s3" if get_settings().data_bucket else "local"


def get_spend_ledger(query: str = "") -> SpendLedger:
    """Daily X read counter, append-only in the history database."""
    return DbLedger(get_settings().x_cost_per_read_usd, query=query)


def get_x_source(
    query: str = "",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> XSearchSource:
    """Fresh guard per request so the per-fetch cap starts at zero each time."""
    settings = get_settings()
    require_durable_spend_storage()
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
        query=query,
    )
    return XSearchSource(
        guard=guard,
        client=_http_client(settings.x_api_base_url, token),
        start_time=start_time,
        end_time=end_time,
    )


def get_hf_source() -> HuggingFaceSource:
    """Configured dataset from settings."""
    s = get_settings()
    return HuggingFaceSource(
        dataset=s.hf_dataset,
        config=s.hf_config,
        split=s.hf_split,
        text_column=s.hf_text_column,
        label_column=s.hf_label_column,
        client=_http_client(s.hf_api_base_url),
    )


def get_s3_store() -> S3Store:
    """User-facing save target."""
    settings = get_settings()
    if not settings.data_bucket:
        raise ConfigurationError("DATA_BUCKET is not configured; saving to S3 is unavailable")
    return S3Store(settings.data_bucket, settings.dataset_prefix, _s3_client())


def get_comprehend_rate() -> PriceQuote | None:
    """Current Comprehend sentiment rate from the Price List API (cached 24 h), or ``None``.

    ``None`` means the UI shows "estimate unavailable" rather than a guessed figure.
    """
    from sentiment_prep.pricing.comprehend_price import PRICING_ENDPOINT_REGION, current_rate

    settings = get_settings()
    client: PricingClient | None = None
    if settings.pricing_enabled:
        try:
            client = cast("PricingClient", _boto_client("pricing", PRICING_ENDPOINT_REGION))
        except Exception as exc:  # noqa: BLE001 - no credentials is a normal local state
            logger.warning("pricing_client_unavailable", error=str(exc))
    return current_rate(
        client,
        settings.aws_region,
        settings.pricing_cache_hours,
        settings.pricing_stale_grace_hours,
    )


def get_comprehend_client() -> ComprehendClient | None:
    """``None`` disables the sentiment comparison and labelling rather than failing requests."""
    settings = get_settings()
    if not settings.comprehend_enabled:
        return None
    return cast("ComprehendClient", _boto_client("comprehend", settings.aws_region))


def _s3_client() -> S3Client:
    """Typed S3 client for the stores below."""
    return cast("S3Client", _boto_client("s3", get_settings().aws_region))
