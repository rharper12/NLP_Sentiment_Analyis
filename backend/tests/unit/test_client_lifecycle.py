"""Credential selection is already covered by boundary tests; exercise rotation and ownership."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from sentiment_prep.api import deps
from sentiment_prep.config import Settings


def test_teardown_continues_after_close_failure_without_closing_any_client_twice():
    deps.close_clients()
    broken, healthy = Mock(), Mock()
    broken.close.side_effect = RuntimeError("failed close")
    deps._open_clients.extend([broken, healthy, healthy])
    deps.close_clients()
    deps.close_clients()
    broken.close.assert_called_once()
    healthy.close.assert_called_once()


def test_ssm_cache_expires_rotates_and_closes_owned_clients(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("sentiment_prep.config.monotonic", lambda: clock[0])
    first, second = Mock(), Mock()
    first.get_parameter.return_value = {"Parameter": {"Value": "first"}}
    second.get_parameter.return_value = {"Parameter": {"Value": "rotated"}}
    session = Mock()
    session.client.side_effect = [first, second]
    monkeypatch.setattr("sentiment_prep.config.create_aws_session", lambda settings: session)
    settings = Settings(
        _env_file=None, api_key=None, api_key_ssm_path="/test", aws_region="eu-west-1"
    )
    assert settings.resolve_api_key() == "first"
    clock[0] = 299
    assert settings.resolve_api_key() == "first"
    assert session.client.call_count == 1
    clock[0] = 300
    assert settings.resolve_api_key() == "rotated"
    assert session.client.call_args.kwargs["region_name"] == "eu-west-1"
    first.close.assert_called_once()
    second.close.assert_called_once()


def test_lambda_invocations_reuse_clients_and_real_teardown_closes_once(monkeypatch):
    from sentiment_prep.api.app import app, handler

    deps.close_clients()
    settings = Settings(
        _env_file=None, runtime="lambda", data_bucket="test", comprehend_enabled=True
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    session = Mock()
    s3, comprehend = Mock(), Mock()
    session.client.side_effect = lambda service, **kwargs: s3 if service == "s3" else comprehend
    monkeypatch.setattr(deps, "create_aws_session", lambda settings: session)
    original_http = httpx.Client
    http = original_http(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    close = Mock(wraps=http.close)
    monkeypatch.setattr(http, "close", close)
    monkeypatch.setattr(deps.httpx, "Client", lambda **kwargs: http)
    identities = []

    def probe():
        repo, checkpoints = deps.get_repository(), deps.get_checkpoint_store()
        assert repo._client is checkpoints._client is deps._s3_client()
        identities.append(
            (
                id(repo),
                id(checkpoints),
                id(deps.get_comprehend_client()),
                id(deps._http_client("https://offline.test")),
            )
        )
        return {"ok": True}

    app.add_api_route("/client-lifetime-test", probe)
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/client-lifetime-test",
        "rawQueryString": "",
        "headers": {"host": "localhost"},
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/client-lifetime-test",
                "sourceIp": "127.0.0.1",
                "protocol": "HTTP/1.1",
            },
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }
    context = SimpleNamespace(aws_request_id="test", get_remaining_time_in_millis=lambda: 30000)
    try:
        assert handler(event, context)["statusCode"] == 200
        assert handler(event, context)["statusCode"] == 200
        assert identities[0] == identities[1]
        assert session.client.call_count == 2
        s3.close.assert_not_called()
        comprehend.close.assert_not_called()
        close.assert_not_called()
        deps.close_clients()
        deps.close_clients()
        s3.close.assert_called_once()
        comprehend.close.assert_called_once()
        close.assert_called_once()
        assert deps.get_repository.cache_info().currsize == 0
        assert deps.get_checkpoint_store.cache_info().currsize == 0
    finally:
        app.router.routes.pop()
        deps.close_clients()
