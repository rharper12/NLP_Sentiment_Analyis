"""Shared SQL reservations, process replacement, and PostgreSQL runtime packaging."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sentiment_prep.config import Settings
from sentiment_prep.errors import ConfigurationError
from sentiment_prep.history import db
from sentiment_prep.history.models import Base, SpendDay, SpendEntry
from sentiment_prep.history.services import DbLedger


@pytest.mark.parametrize("url", [None, "sqlite:////tmp/explicit.sqlite3", "sqlite:///:memory:"])
def test_lambda_rejects_ephemeral_paid_storage(monkeypatch, url):
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(
            _env_file=None, runtime="lambda", database_url=url, database_url_ssm_path=None
        ),
    )
    with pytest.raises(ConfigurationError, match="shared PostgreSQL"):
        db.require_durable_spend_storage()


def test_postgresql_driver_loads_without_connecting():
    engine = db.build_engine("postgresql+psycopg://user:pass@localhost/test")
    assert engine.dialect.driver == "psycopg"
    assert engine.dialect.dbapi.__name__ == "psycopg"
    engine.dispose()
    with pytest.raises(ConfigurationError, match="postgresql\\+psycopg"):
        db.build_engine("postgresql://user:pass@localhost/test")


def test_unavailable_shared_database_fails_before_x_client_construction(monkeypatch):
    from sqlalchemy.exc import OperationalError

    from sentiment_prep.api import deps

    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            runtime="lambda",
            database_url="postgresql+psycopg://user:pass@unavailable/test",
        ),
    )

    def unavailable():
        raise OperationalError("connection", {}, RuntimeError("offline"))

    monkeypatch.setattr(db, "get_engine", unavailable)
    monkeypatch.setattr(deps, "_http_client", lambda *args: pytest.fail("X must not be contacted"))
    with pytest.raises(ConfigurationError, match="storage is unavailable"):
        deps.get_x_source("test")


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_independent_clients_share_atomic_allowance_after_restart(tmp_path, backend):
    url = (
        os.environ.get("TEST_POSTGRES_URL")
        if backend == "postgresql"
        else f"sqlite:///{tmp_path / 'ledger.db'}"
    )
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to a disposable PostgreSQL database")
    first, second = db.build_engine(url), db.build_engine(url)
    Base.metadata.create_all(first)
    day = "2040-03-04"
    with first.begin() as connection:
        connection.execute(delete(SpendEntry).where(SpendEntry.day == date.fromisoformat(day)))
        connection.execute(delete(SpendDay).where(SpendDay.day == date.fromisoformat(day)))

    def ledger(engine):
        @contextmanager
        def transaction():
            with Session(engine, expire_on_commit=False) as session, session.begin():
                yield session

        return DbLedger(0.005, session_scope=transaction)

    a, b = ledger(first), ledger(second)
    assert a.reserve(day, 40, 50)
    assert a.settle(day, 40, 40, "first") == 40
    barrier = Barrier(2)

    def reserve(client):
        barrier.wait(timeout=5)
        return client.reserve(day, 10, 50)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [a, b])) == [False, True]
    assert a.reserved(day) == b.reserved(day) == 50
    first.dispose()
    second.dispose()
    replacement = db.build_engine(url)
    reloaded = ledger(replacement)
    assert reloaded.reserved(day) == 50
    assert not reloaded.reserve(day, 1, 50)
    assert reloaded.settle(day, 10, 6, "settled after restart") == 46
    assert reloaded.reserve(day, 4, 50)
    replacement.dispose()
