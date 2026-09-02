"""Bounded retry for transient Supabase/Postgres connectivity blips -- and only those."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import app.db.retry as retry_mod
from app.config import Settings
from app.db.database import Base, _pg_engine_config, build_engine, db_connection_mode, db_preflight
from app.db.retry import is_transient_db_error, run_in_session


class _PgErr(Exception):
    """Stand-in for a psycopg error carrying a SQLSTATE."""

    def __init__(self, message: str, sqlstate: str | None = None):
        super().__init__(message)
        self.sqlstate = sqlstate


def _op(msg: str, sqlstate: str | None = None) -> OperationalError:
    return OperationalError("SELECT 1", {}, _PgErr(msg, sqlstate))


# --- classification ------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        _op("could not receive data from server: Operation timed out"),
        _op("server closed the connection unexpectedly"),
        _op("connection failure", "08006"),
        _op("terminating connection due to administrator command", "57P01"),
        _op("remaining connection slots are reserved", "53300"),
        _op("deadlock detected", "40P01"),
        InterfaceError("s", {}, _PgErr("connection already closed")),
    ],
)
def test_transient_errors_are_retryable(exc):
    assert is_transient_db_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        IntegrityError("s", {}, _PgErr("duplicate key value violates unique constraint", "23505")),
        ProgrammingError("s", {}, _PgErr('column "x" does not exist', "42703")),
        _op("password authentication failed", "28P01"),
        _op("permission denied for table job_runs", "42501"),
        _op('database "nope" does not exist', "3D000"),
        _op("invalid input syntax for type integer", "22P02"),
        ValueError("not a db error at all"),
    ],
)
def test_non_transient_errors_are_not_retryable(exc):
    assert is_transient_db_error(exc) is False


# --- run_in_session retry behaviour ------------------------------------------------------
@pytest.fixture
def fast_retry(monkeypatch):
    """3 attempts, no real sleeping."""
    monkeypatch.setattr(
        retry_mod, "get_settings", lambda: Settings(db_retry_attempts=3, db_retry_max_wait_seconds=1)
    )
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture
def fake_sessions(monkeypatch):
    """Replace SessionLocal with a factory of no-op sessions; track how many were opened."""
    opened = []

    class _FakeSession:
        def __init__(self):
            opened.append(self)
            self.committed = self.rolled_back = self.closed = False

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    monkeypatch.setattr("app.db.database.SessionLocal", _FakeSession)
    return opened


def test_run_in_session_retries_a_transient_failure_then_succeeds(fast_retry, fake_sessions):
    calls = {"n": 0}

    def fn(_session):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _op("could not receive data from server: connection timed out")
        return "ok"

    assert run_in_session(fn, description="unit") == "ok"
    assert calls["n"] == 2
    assert len(fake_sessions) == 2  # a FRESH session per attempt
    assert fake_sessions[0].rolled_back and fake_sessions[0].closed
    assert fake_sessions[1].committed and fake_sessions[1].closed


def test_run_in_session_gives_up_after_the_attempt_cap(fast_retry, fake_sessions):
    calls = {"n": 0}

    def fn(_session):
        calls["n"] += 1
        raise _op("server closed the connection unexpectedly", "08006")

    with pytest.raises(OperationalError):
        run_in_session(fn, description="unit")
    assert calls["n"] == 3  # db_retry_attempts, not infinite
    assert len(fake_sessions) == 3


def test_run_in_session_does_not_retry_a_data_error(fast_retry, fake_sessions):
    calls = {"n": 0}

    def fn(_session):
        calls["n"] += 1
        raise IntegrityError("s", {}, _PgErr("duplicate key", "23505"))

    with pytest.raises(IntegrityError):
        run_in_session(fn, description="unit")
    assert calls["n"] == 1  # failed loudly on the first try
    assert len(fake_sessions) == 1


# --- idempotency: "commit landed but the client never got the ack" -------------------------
def test_retry_after_a_post_commit_blip_does_not_double_write(tmp_path, monkeypatch):
    """open_run is keyed by run_key: attempt 1 writes the row then the ack is lost; attempt 2
    finds the same row and re-attaches. Exactly one job_runs row, no duplicate."""
    from sqlalchemy import func, select

    from app.db.models import JobRun
    from app.ppi.job_run_lifecycle import derive_run_key, open_run

    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    Base.metadata.create_all(engine)
    RealSession = sessionmaker(engine, expire_on_commit=False, future=True)
    monkeypatch.setattr("app.db.database.SessionLocal", RealSession)
    monkeypatch.setattr(retry_mod, "get_settings", lambda: Settings(db_retry_attempts=3, db_retry_max_wait_seconds=1))
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    run_key = derive_run_key("primary", None)
    state = {"attempt": 0}

    def _open(session):
        state["attempt"] += 1
        job, outcome = open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
        if state["attempt"] == 1:
            session.commit()  # the write really lands...
            raise _op("could not receive data from server")  # ...but the client never hears back
        return outcome

    outcome = run_in_session(_open, description="open run (idempotent)")
    assert state["attempt"] == 2
    assert outcome in {"reattached", "reset", "created"}
    with RealSession() as s:
        assert s.scalar(select(func.count()).select_from(JobRun).where(JobRun.run_key == run_key)) == 1


# --- connection mode parsing ------------------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("sqlite:///data/x.db", "sqlite"),
        ("postgresql://u:p@aws-0-ca-central-1.pooler.supabase.com:5432/postgres", "session-pooler"),
        ("postgres://u:p@aws-0-eu-west-1.pooler.supabase.com:6543/postgres", "transaction-pooler"),
        ("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres", "direct-postgres"),
        ("postgresql+psycopg://u:p@10.0.0.5:5432/ppi", "direct-postgres"),
    ],
)
def test_db_connection_mode(url, expected):
    assert db_connection_mode(url) == expected


# --- engine configuration -------------------------------------------------------------------
def test_transaction_pooler_config_disables_prepared_statements_and_uses_nullpool():
    connect_args, engine_kwargs = _pg_engine_config(
        "postgresql://u:p@aws-0-ca-central-1.pooler.supabase.com:6543/postgres"
    )
    assert engine_kwargs["poolclass"] is NullPool
    assert connect_args["prepare_threshold"] is None  # Supavisor transaction mode has no PREPARE
    # no QueuePool sizing when the pooler owns pooling
    assert "pool_recycle" not in engine_kwargs and "pool_size" not in engine_kwargs
    assert engine_kwargs["pool_pre_ping"] is True


def test_session_pooler_config_is_a_recycling_keepalive_queue_pool():
    connect_args, engine_kwargs = _pg_engine_config(
        "postgresql://u:p@aws-0-ca-central-1.pooler.supabase.com:5432/postgres"
    )
    assert "poolclass" not in engine_kwargs  # default QueuePool
    assert engine_kwargs["pool_recycle"] == Settings().db_pool_recycle_seconds
    assert engine_kwargs["pool_pre_ping"] is True
    assert engine_kwargs["pool_use_lifo"] is True
    assert connect_args["connect_timeout"] == Settings().db_connect_timeout_seconds
    assert connect_args["keepalives"] == 1
    assert connect_args["application_name"] == "ppi-pipeline"
    assert connect_args["options"] == f"-c statement_timeout={Settings().db_statement_timeout_ms}"
    assert "prepare_threshold" not in connect_args


def test_build_engine_wires_the_pg_config(monkeypatch):
    eng = build_engine("postgresql://u:p@aws-0-ca-central-1.pooler.supabase.com:6543/postgres")
    assert isinstance(eng.pool, NullPool)
    eng2 = build_engine("postgresql://u:p@aws-0-ca-central-1.pooler.supabase.com:5432/postgres")
    assert not isinstance(eng2.pool, NullPool)
    assert eng2.pool._recycle == Settings().db_pool_recycle_seconds


def test_sqlite_engine_is_unchanged(tmp_path):
    eng = build_engine(f"sqlite:///{tmp_path / 's.db'}")
    with eng.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


# --- db_preflight ---------------------------------------------------------------------------
def test_db_preflight_ok_on_sqlite(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'pf.db'}")
    monkeypatch.setattr("app.db.database.SessionLocal", sessionmaker(engine, future=True))
    monkeypatch.setattr("app.db.database.db_connection_mode", lambda *a, **k: "sqlite")
    assert db_preflight() == "sqlite"


def test_db_preflight_raises_on_a_real_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(retry_mod, "get_settings", lambda: Settings(db_retry_attempts=2, db_retry_max_wait_seconds=1))
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    class _Boom:
        def execute(self, *_a, **_k):
            raise ProgrammingError("SELECT 1", {}, _PgErr("relation does not exist", "42P01"))

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.db.database.SessionLocal", _Boom)
    with pytest.raises(ProgrammingError):
        db_preflight()
