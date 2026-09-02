from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _normalize_database_url(database_url: str) -> str:
    # Render and many managed providers expose postgresql:// URLs. Explicitly select
    # psycopg v3, which is the driver installed by this repository.
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


def _ensure_sqlite_parent(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        db_file = Path(database_url.replace("sqlite:///", ""))
        db_file.parent.mkdir(parents=True, exist_ok=True)


def db_connection_mode(database_url: str | None = None) -> str:
    """Classify the connection target for diagnostics and engine tuning -- no credentials.

    * ``sqlite``               -- local file.
    * ``transaction-pooler``   -- Supabase Supavisor transaction mode (``...pooler.supabase.com:6543``):
      per-transaction connections, ideal for many transient clients (a cloud runner), but does
      NOT support server-side prepared statements.
      https://supabase.com/docs/guides/database/connecting-to-postgres
    * ``session-pooler``       -- Supavisor session mode (``...pooler.supabase.com:5432``): a
      dedicated connection for the session's lifetime, IPv4 on every tier. PPI's current target.
    * ``direct-postgres``      -- a non-pooled Postgres endpoint (``db.<ref>.supabase.co:5432`` etc).
    """
    url = _normalize_database_url(database_url or get_settings().database_url)
    if url.startswith("sqlite"):
        return "sqlite"
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if "pooler.supabase.com" in host:
        return "transaction-pooler" if port == 6543 else "session-pooler"
    return "direct-postgres"


def build_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    raw_url = database_url or settings.database_url
    url = _normalize_database_url(raw_url)
    _ensure_sqlite_parent(url)

    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
            pool_pre_ping=True,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return eng

    # --- Postgres / Supabase Supavisor pooler -------------------------------------------------
    connect_args, engine_kwargs = _pg_engine_config(raw_url)
    return create_engine(url, connect_args=connect_args, **engine_kwargs)


def _pg_engine_config(raw_url: str) -> tuple[dict[str, object], dict[str, object]]:
    """Compute (connect_args, create_engine kwargs) for a Postgres/pooler URL. Split out so the
    resilience choices below are directly unit-testable."""
    settings = get_settings()
    mode = db_connection_mode(raw_url)
    connect_args: dict[str, object] = {
        # Fail a dead connect in ~10s instead of hanging on the OS default (~2 min) -- the
        # failure that killed a multi-minute run was a fresh connect that never timed out.
        "connect_timeout": settings.db_connect_timeout_seconds,
        # TCP keepalives so a connection that goes idle while the pipeline waits on two LLM
        # provider calls is not silently reaped by Supavisor / cloud NAT before its next use.
        "keepalives": 1,
        "keepalives_idle": settings.db_keepalives_idle_seconds,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        "application_name": "ppi-pipeline",
    }
    if settings.db_statement_timeout_ms and settings.db_statement_timeout_ms > 0:
        connect_args["options"] = f"-c statement_timeout={settings.db_statement_timeout_ms}"

    engine_kwargs: dict[str, object] = {
        "future": True,
        # Verify a pooled connection with SELECT 1 on checkout; a dead one is discarded and
        # replaced transparently.
        "pool_pre_ping": True,
    }

    if mode == "transaction-pooler":
        # Supavisor transaction mode hands out a connection per transaction and does not support
        # prepared statements (https://supabase.com/docs/guides/database/connecting-to-postgres)
        # -- let it do the pooling (NullPool) and turn psycopg's auto-prepare off. This branch
        # only activates if DATABASE_URL points at :6543; today it does not, but the eventual
        # cloud runner should use it and then needs no further code change.
        engine_kwargs["poolclass"] = NullPool
        connect_args["prepare_threshold"] = None
    else:
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_pool_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            # Recycle a connection before it hits the pooler/NAT idle cull, so a long gap
            # between the twice-daily runs never leaves a stale connection to fail on first use.
            pool_recycle=settings.db_pool_recycle_seconds,
            # Reuse the most-recently-returned connection: idle ones age out and get recycled
            # instead of being handed to the next batch run stale.
            pool_use_lifo=True,
        )
    return connect_args, engine_kwargs


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """A committed unit of work.

    The connection is checked out lazily on first use and verified live by ``pool_pre_ping``;
    for a retried unit of work that must survive a transient pooler/network blip, use
    ``app.db.retry.run_in_session`` instead -- a failed transaction's uncommitted writes cannot
    be replayed on the same session, so retry needs a fresh one.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_preflight() -> str:
    """``SELECT 1`` at pipeline start for early detection of a DB outage -- retried on transient
    errors, raised immediately on a real one (auth, bad database, ...). Returns the connection
    mode. Not proof the whole run will stay connected; that is what the retry layer is for."""
    from app.db.retry import run_in_session  # local import: retry.py imports this module

    run_in_session(lambda s: s.execute(text("SELECT 1")).scalar(), description="preflight SELECT 1")
    mode = db_connection_mode()
    print(f"[db] preflight OK (connection_mode={mode})")
    return mode


def db_diagnostics() -> dict[str, object]:
    """Sanitized connectivity snapshot for job logs / the run summary -- never a URL or a
    credential. ``pool`` is absent for NullPool (transaction-pooler mode)."""
    settings = get_settings()
    diag: dict[str, object] = {
        "connection_mode": db_connection_mode(),
        "connect_timeout_s": settings.db_connect_timeout_seconds,
        "pool_recycle_s": settings.db_pool_recycle_seconds,
        "statement_timeout_ms": settings.db_statement_timeout_ms,
        "retry_attempts": settings.db_retry_attempts,
    }
    status = getattr(engine.pool, "status", None)
    if callable(status):
        try:
            diag["pool"] = status()
        except Exception:  # pragma: no cover - diagnostics must never raise
            pass
    return diag
