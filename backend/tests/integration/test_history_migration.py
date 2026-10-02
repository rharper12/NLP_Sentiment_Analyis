"""Real PostgreSQL schema upgrades and X request identities, with X itself mocked."""

import os
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from sentiment_prep.api import collection, deps
from sentiment_prep.api.schemas import LoadRequest
from sentiment_prep.config import Settings
from sentiment_prep.history import db, services
from sentiment_prep.history.models import Base, DatasetRun, PipelineRun
from sentiment_prep.sources.spend_guard import InMemoryLedger, SpendGuard
from sentiment_prep.sources.x_search import XSearchSource
from sentiment_prep.storage.checkpoints import LocalCheckpointStore
from sentiment_prep.storage.repository import InMemoryRepository


@pytest.fixture
def postgres():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to a disposable PostgreSQL database")
    admin = db.build_engine(url)
    schema = "migration_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.mark.parametrize("legacy", [True, False])
def test_schema_upgrade_preserves_history_and_accepts_all_collection_id_lengths(postgres, legacy):
    if legacy:
        Base.metadata.create_all(postgres)
        with postgres.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE dataset_run ALTER COLUMN dataset_id TYPE VARCHAR(32)"
            )
        with Session(postgres) as session, session.begin():
            row = DatasetRun(dataset_id="previous-upload", source_type="csv", record_count=1)
            session.add(row)
            session.flush()
            session.add(
                PipelineRun(
                    dataset_pk=row.id,
                    applied_steps=["lowercase"],
                    records_out=1,
                    vocab_before=1,
                    vocab_after=1,
                    duration_ms=1,
                )
            )
    db.initialize_schema(postgres)
    db.initialize_schema(postgres)  # Warm/replacement initialization must be idempotent.
    column = next(
        c for c in inspect(postgres).get_columns("dataset_run") if c["name"] == "dataset_id"
    )
    assert getattr(column["type"], "length", None) is None
    with Session(postgres) as session, session.begin():
        if legacy:
            previous = session.scalar(
                select(DatasetRun).where(DatasetRun.dataset_id == "previous-upload")
            )
            assert previous.record_count == 1 and previous.runs[0].applied_steps == ["lowercase"]
        for request_id in (uuid4().hex, str(uuid4()), "r" * 64):
            session.add(DatasetRun(dataset_id="x-" + request_id, source_type="x", record_count=1))


def test_browser_uuid_collection_persists_and_retries_without_another_paid_read(
    postgres, tmp_path, monkeypatch
):
    db.initialize_schema(postgres)

    @contextmanager
    def session_scope():
        with Session(postgres, expire_on_commit=False) as session, session.begin():
            yield session

    monkeypatch.setattr(services, "session", session_scope)
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "123", "text": "I really love this new phone"}],
                "meta": {"result_count": 1},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(reply), base_url="https://x.test/2") as client:
        monkeypatch.setattr(
            deps,
            "get_x_source",
            lambda *args: XSearchSource(SpendGuard(InMemoryLedger(), 100, 3000, 0.005), client),
        )
        body = LoadRequest(source="x", query="phone", limit=1, request_id=str(uuid4()))
        repo = InMemoryRepository()
        checkpoints = LocalCheckpointStore(tmp_path)
        settings = Settings(_env_file=None)
        first = collection.collect_x(body, repo, checkpoints, settings)
        second = collection.collect_x(body, repo, checkpoints, settings)
    assert first.dataset_id == second.dataset_id == "x-" + body.request_id
    assert len(calls) == 1 and first.record_count == second.record_count == 1
    with session_scope() as session:
        rows = session.scalars(select(DatasetRun)).all()
        assert (
            len(rows) == 1 and rows[0].dataset_id == first.dataset_id and rows[0].record_count == 1
        )
