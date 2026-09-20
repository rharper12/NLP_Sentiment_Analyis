"""Engine and session factory.

``DATABASE_URL`` picks the backend: SQLite in ``./data`` locally, SQLite on ``/tmp`` in Lambda
(ephemeral, reported by ``/health``), or Postgres when the operator supplies a URL. Tables are
created on first use; there is no separate migration step because the schema is tiny and additive.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from sentiment_prep.config import get_settings
from sentiment_prep.errors import ConfigurationError
from sentiment_prep.history.models import Base
from sentiment_prep.logging_config import get_logger

logger = get_logger(__name__)


def default_sqlite_url(runtime: str) -> str:
    """Local file under the repo for dev, ``/tmp`` for Lambda (the only writable path there)."""
    path = (
        Path("/tmp/sentiment_prep.sqlite3")
        if runtime == "lambda"
        else Path(__file__).resolve().parents[3] / "data" / "sentiment_prep.sqlite3"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """One engine per process; creates tables on first call."""
    settings = get_settings()
    url = settings.resolve_database_url() or default_sqlite_url(settings.runtime)
    engine = build_engine(url)
    if url.startswith("sqlite"):
        # SQLite needs WAL for concurrent readers and a busy timeout for the ledger writes.
        @event.listens_for(engine, "connect")
        def _pragmas(connection: DBAPIConnection, _record: object) -> None:
            """Apply the connection settings SQLite needs for concurrent readers."""
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=5000")

    started = time.perf_counter()
    Base.metadata.create_all(engine)
    logger.info(
        "database_ready",
        backend=engine.dialect.name,
        ephemeral=is_ephemeral(),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return engine


def build_engine(url: str) -> Engine:
    """Construct the supported drivers with bounded connection and statement waits."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        if parsed.drivername != "postgresql+psycopg":
            raise ConfigurationError("Use a postgresql+psycopg:// DATABASE_URL")
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_timeout=2,
            connect_args={
                "connect_timeout": 2,
                "options": "-c statement_timeout=2000 -c lock_timeout=1000",
            },
        )
    return create_engine(url, pool_pre_ping=True, future=True)


def require_durable_spend_storage() -> None:
    """Paid Lambda reads require the shared PostgreSQL ledger; never fall back to /tmp."""
    settings = get_settings()
    if settings.runtime == "lambda":
        url = settings.resolve_database_url()
        if not url or make_url(url).drivername != "postgresql+psycopg":
            raise ConfigurationError(
                "Paid X collection in Lambda requires shared PostgreSQL storage"
            )
        # Constructing the engine loads the driver and verifies schema/connectivity. A failure
        # aborts before reserving budget or contacting X; there is no fallback database.
        try:
            get_engine()
        except SQLAlchemyError as exc:
            raise ConfigurationError(
                "Shared spend storage is unavailable; X collection refused"
            ) from exc


def is_ephemeral() -> bool:
    """True when history lives on Lambda's ``/tmp`` and will vanish on the next cold start."""
    settings = get_settings()
    url = settings.resolve_database_url()
    return settings.runtime == "lambda" and (
        not url or make_url(url).get_backend_name() == "sqlite"
    )


def backend_name() -> str:
    """Dialect name for the health endpoint."""
    return get_engine().dialect.name


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    """One factory per process, bound to the cached engine."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session() -> Iterator[Session]:
    """Unit of work: commit on success, roll back on error, always close."""
    with _session_factory()() as s:
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
