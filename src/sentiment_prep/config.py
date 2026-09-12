"""All runtime configuration in one place.

Values come from environment variables (locally via ``.env``, in Lambda via the SAM
template). The X bearer token is the only secret: it may be supplied directly as
``X_BEARER_TOKEN`` for local work, or indirectly via ``X_BEARER_TOKEN_SSM_PATH`` so the
deployed function reads it from SSM Parameter Store. Swapping X accounts is therefore a
one-line change in ``.env`` or ``samconfig.toml``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)


class Settings(BaseSettings):
    """Typed view of the environment."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    runtime: Literal["local", "lambda"] = "local"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # Storage
    data_bucket: str | None = None
    dataset_prefix: str = "datasets"

    # X API v2. Recent search only covers the last seven days and every returned post is
    # billed, so two spend caps guard against runaway pagination.
    x_bearer_token: str | None = None
    x_bearer_token_ssm_path: str | None = None
    x_api_base_url: str = "https://api.x.com/2"
    x_max_reads_per_fetch: int = Field(default=1000, ge=1)
    x_max_reads_per_day: int = Field(default=3000, ge=1)
    x_cost_per_read_usd: float = 0.005

    # Hugging Face datasets-server (no auth needed for public datasets).
    hf_api_base_url: str = "https://datasets-server.huggingface.co"
    hf_dataset: str = "cardiffnlp/tweet_eval"
    hf_config: str = "sentiment"
    hf_split: str = "train"
    hf_text_column: str = "text"
    hf_label_column: str = "label"

    # Checkpoints: CSV snapshots written as soon as data exists (collected, processed, labelled)
    # so a crash after a paid step never loses what was paid for. Local folder is gitignored.
    checkpoint_dir: str = "data/checkpoints"
    # Bundles kept in memory when running locally; oldest is evicted past this.
    local_repository_size: int = Field(default=20, ge=1)
    checkpoint_prefix: str = "checkpoints"

    # AWS ML services. Any of these can be disabled to run without an AWS account.
    comprehend_enabled: bool = True
    # Comprehend bills per 100-character unit with a 3-unit minimum per document; these are the
    # billing *structure*. The per-unit *price* is never hard-coded: it comes from the AWS Price
    # List API (pricing:GetProducts) and is cached for pricing_cache_hours.
    comprehend_unit_chars: int = 100
    comprehend_min_units: int = 3
    pricing_enabled: bool = True
    pricing_cache_hours: int = 24

    # Operator-only details (checkpoint paths, which services are enabled) are returned by
    # /health only when this is true. Defaults to local runtime; deployed builds hide them.
    diagnostics: bool | None = None

    bedrock_enabled: bool = True
    bedrock_text_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_sample_size: int = Field(default=50, ge=5, le=200)

    # Shared secret required on every request when set. Unset locally; required in Lambda.
    api_key: str | None = None
    api_key_ssm_path: str | None = None

    cors_origins: str = "http://localhost:5173"

    # History database. Empty = SQLite (./data locally, /tmp in Lambda, which is ephemeral).
    database_url: str | None = None
    database_url_ssm_path: str | None = None

    @property
    def diagnostics_enabled(self) -> bool:
        """Explicit setting wins; otherwise only the local runtime exposes diagnostics."""
        return self.runtime == "local" if self.diagnostics is None else self.diagnostics

    def resolve_api_key(self) -> str | None:
        """Env var first, then SSM, else ``None`` (authentication disabled)."""
        if self.api_key:
            return self.api_key
        return self._ssm_value(self.api_key_ssm_path) if self.api_key_ssm_path else None

    def resolve_database_url(self) -> str | None:
        """Env var first, then SSM, else ``None`` (caller falls back to SQLite)."""
        if self.database_url:
            return self.database_url
        if not self.database_url_ssm_path:
            return None
        return self._ssm_value(self.database_url_ssm_path)

    def _ssm_value(self, path: str) -> str:
        import boto3

        logger.info("secret_from_ssm", path=path)
        client = boto3.client("ssm", region_name=self.aws_region)
        value: str = client.get_parameter(Name=path, WithDecryption=True)["Parameter"]["Value"]
        return value

    def resolve_x_bearer_token(self) -> str | None:
        """Return the X token from the environment, else from SSM, else ``None``.

        SSM is only consulted when the direct variable is absent, so a developer can override
        the deployed account locally without touching Parameter Store.
        """
        if self.x_bearer_token:
            return self.x_bearer_token
        if not self.x_bearer_token_ssm_path:
            return None
        return self._ssm_value(self.x_bearer_token_ssm_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings so the environment is parsed once per process."""
    return Settings()
