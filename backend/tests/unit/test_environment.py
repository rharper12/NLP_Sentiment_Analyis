"""The offline suite must not inherit a developer's service configuration."""

from sentiment_prep.config import Settings


def test_developer_dotenv_cannot_enable_external_services(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DATA_BUCKET=developer-bucket\n"
        "X_BEARER_TOKEN=developer-token\n"
        "API_KEY=developer-key\n"
        "DATABASE_URL=postgresql+psycopg://localhost/developer\n"
        "COMPREHEND_ENABLED=true\n"
        "PRICING_ENABLED=true\n"
    )

    settings = Settings()

    assert settings.data_bucket is None
    assert settings.resolve_x_bearer_token() is None
    assert settings.resolve_api_key() is None
    assert settings.resolve_database_url().startswith("sqlite:///")
    assert not settings.comprehend_enabled
    assert not settings.pricing_enabled
