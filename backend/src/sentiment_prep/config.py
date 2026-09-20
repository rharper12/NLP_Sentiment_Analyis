"""All runtime configuration in one place.

Values come from environment variables (locally via ``.env``, in Lambda via SAM).
Operator credentials, X tokens and database URLs can resolve from SSM Parameter Store.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Literal

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

from sentiment_prep.aws import create_aws_session
from sentiment_prep.budget import AWS_CONFIG
from sentiment_prep.errors import ConfigurationError


class Settings(BaseSettings):
    """Typed view of the environment."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    runtime: Literal["local", "lambda"] = "local"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"
    # Named profile from ~/.aws/config, including SSO profiles. Unset uses the default credential
    # chain, which is what Lambda relies on. Setting it here rather than exporting AWS_PROFILE
    # keeps every knob in one file, and boto3 never reads this project's .env itself.
    aws_profile: str | None = None
    # Static IAM user keys, for a machine with no SSO. A profile is preferred: keys in a file are
    # long-lived and easy to leak, while an SSO session expires on its own. Whichever is set,
    # nothing is logged and nothing reaches the browser.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    @property
    def has_static_keys(self) -> bool:
        """True when an access key pair is configured, which takes precedence over a profile."""
        return bool(self.aws_access_key_id and self.aws_secret_access_key)

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
    x_cost_per_read_usd: float = Field(default=0.005, ge=0, allow_inf_nan=False)

    # Hugging Face datasets-server (no auth needed for public datasets).
    hf_api_base_url: str = "https://datasets-server.huggingface.co"
    hf_dataset: str = "cardiffnlp/tweet_eval"
    hf_config: str = "sentiment"
    hf_split: str = "train"
    hf_text_column: str = "text"
    hf_label_column: str = "label"

    # Duplicate removal at collection. Threshold 1.0 keeps only exact-after-normalisation
    # matching; lower values also drop near-duplicates by token overlap.
    dedupe_enabled: bool = True
    dedupe_similarity: float = Field(default=0.9, gt=0.0, le=1.0)

    # Checkpoints: CSV snapshots written as soon as data exists (collected, processed, labelled)
    # alongside the working journal. Provider success before persistence can still be ambiguous.
    checkpoint_dir: str = "data/checkpoints"
    checkpoint_prefix: str = "checkpoints"

    # AWS ML services. Any of these can be disabled to run without an AWS account.
    comprehend_enabled: bool = True
    # Comprehend bills per 100-character unit with a 3-unit minimum per document; these are the
    # billing *structure*. The per-unit *price* is never hard-coded: it comes from the AWS Price
    # List API (pricing:GetProducts) and is cached for pricing_cache_hours.
    comprehend_unit_chars: int = 100
    comprehend_min_units: int = 3
    pricing_enabled: bool = True
    pricing_cache_hours: int = Field(default=24, ge=0)
    pricing_stale_grace_hours: int = Field(default=48, ge=0)

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

    # Values expire after five minutes; failures are never cached and secrets never logged.
    _secret_cache: dict[str, tuple[float, str]] = PrivateAttr(default_factory=dict)
    _secret_lock: Lock = PrivateAttr(default_factory=Lock)

    def _ssm_value(self, path: str) -> str:
        with self._secret_lock:
            cached = self._secret_cache.get(path)
            if cached and monotonic() < cached[0]:
                return cached[1]
            try:
                client = create_aws_session(self).client(
                    "ssm", region_name=self.aws_region, config=AWS_CONFIG
                )
                try:
                    response = client.get_parameter(Name=path, WithDecryption=True)
                finally:
                    close = getattr(client, "close", None)
                    if close is not None:
                        close()
            except Exception:  # noqa: BLE001 - never expose provider exceptions containing secrets
                raise ConfigurationError(
                    "Cannot read SSM configuration. Check AWS credentials, region "
                    "and parameter permissions."
                ) from None
            parameter = response.get("Parameter") if isinstance(response, dict) else None
            value = parameter.get("Value") if isinstance(parameter, dict) else None
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError("SSM configuration must contain a nonempty string value")
            self._secret_cache[path] = (monotonic() + 300, value)
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
