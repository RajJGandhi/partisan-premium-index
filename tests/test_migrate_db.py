from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import scripts.migrate_db as migrate_db
from app.db.models import LLMForecast, Market


def _make_pre_migration_sqlite_db(tmp_path):
    """Simulates an existing local SQLite dev DB created before this change: the OLD 2-column
    (market_id, run_slot) unique constraint baked into the CREATE TABLE statement, and missing
    every column later added via ADDITIVE_COLUMNS (reviewed_status etc.) -- the realistic worst
    case migrate() must handle, since a genuinely old, never-migrated DB would look like this."""
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_migration.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE markets (id INTEGER PRIMARY KEY, platform_market_id VARCHAR, question TEXT, "
                "active BOOLEAN DEFAULT 1, closed BOOLEAN DEFAULT 0)"
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE llm_forecasts (
                    id INTEGER PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    run_key VARCHAR(150),
                    run_slot VARCHAR(60) NOT NULL,
                    trigger_type VARCHAR(30) DEFAULT 'manual',
                    generated_at DATETIME,
                    model_provider VARCHAR(30) NOT NULL,
                    model_name VARCHAR(100) NOT NULL,
                    prompt_version VARCHAR(30) NOT NULL,
                    fair_value FLOAT,
                    status VARCHAR(30) DEFAULT 'PENDING',
                    retries INTEGER DEFAULT 0,
                    CONSTRAINT uq_llm_forecast_market_run_slot UNIQUE (market_id, run_slot)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO llm_forecasts (id, market_id, run_key, run_slot, generated_at, model_provider, "
                "model_name, prompt_version, fair_value, status, retries) VALUES "
                "(1, 1, 'r', '2026-08-12:primary', '2026-08-12 13:00:00', 'ollama', 'qwen3:8b', "
                "'fair_value_v0.1', 0.45, 'OK', 0)"
            )
        )
    return engine


def test_old_two_column_constraint_rejects_a_second_provider_row_before_migration(tmp_path):
    """Proves the bug this migration fixes actually exists under the pre-migration schema shape."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = _make_pre_migration_sqlite_db(tmp_path)
    with engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO llm_forecasts (id, market_id, run_key, run_slot, model_provider, model_name, "
                "prompt_version, fair_value, status, retries) VALUES "
                "(2, 1, 'r', '2026-08-12:primary', 'openrouter', 'deepseek/deepseek-v4-flash-0731', "
                "'fair_value_v0.1', 0.71, 'OK', 0)"
            )
        )


def test_migrate_rebuilds_sqlite_uniqueness_and_preserves_data(tmp_path, monkeypatch):
    """End-to-end: the real migrate() entry point, against a realistic pre-existing SQLite DB
    missing both the widened constraint and every later-added column."""
    engine = _make_pre_migration_sqlite_db(tmp_path)
    monkeypatch.setattr(migrate_db, "engine", engine)

    migrate_db.migrate()

    # Original row preserved, and backfilled with the new columns' real (DDL) defaults.
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT market_id, run_slot, model_provider, fair_value, reviewed_status FROM llm_forecasts")
        ).fetchone()
    assert row.model_provider == "ollama"
    assert row.fair_value == 0.45
    assert row.reviewed_status == "UNREVIEWED"

    # New constraint shape: same (market_id, run_slot) now allowed for a second provider. Inserted
    # via the ORM (as the real application always does), not raw SQL, since the rebuilt table's
    # NOT-NULL-with-Python-side-only-default columns (e.g. reviewed_status) are only populated by
    # SQLAlchemy's own insert flow, not by a database-level DEFAULT clause.
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session.begin() as session:
        session.add(
            LLMForecast(
                id=2,
                market_id=1,
                run_key="r",
                run_slot="2026-08-12:primary",
                model_provider="openrouter",
                model_name="deepseek/deepseek-v4-flash-0731",
                prompt_version="fair_value_v0.1",
                fair_value=0.71,
                status="OK",
                generated_at=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
            )
        )
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, model_provider FROM llm_forecasts ORDER BY id")).fetchall()
    assert [r.model_provider for r in rows] == ["ollama", "openrouter"]

    # But the same (market_id, run_slot, model_provider) triple is still rejected.
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with Session.begin() as session:
            session.add(
                LLMForecast(
                    id=3,
                    market_id=1,
                    run_key="r",
                    run_slot="2026-08-12:primary",
                    model_provider="ollama",
                    model_name="qwen3:8b",
                    prompt_version="fair_value_v0.1",
                    fair_value=0.90,
                    status="OK",
                    generated_at=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
                )
            )


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    """Running migrate() twice must not error and must not duplicate/lose data."""
    engine = _make_pre_migration_sqlite_db(tmp_path)
    monkeypatch.setattr(migrate_db, "engine", engine)

    migrate_db.migrate()
    migrate_db.migrate()  # old 2-column shape is already gone by now -- must be a clean no-op

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM llm_forecasts")).fetchall()
    assert len(rows) == 1


def test_migrate_on_a_fresh_database_is_a_pure_create(tmp_path, monkeypatch):
    """No pre-existing llm_forecasts table at all: migrate() must not touch the widening logic,
    Base.metadata.create_all already produces the 3-column constraint directly from models.py."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(migrate_db, "engine", engine)

    migrate_db.migrate()

    Session = sessionmaker(engine, expire_on_commit=False)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Q?", rules="R.", enabled=True)
        session.add(market)
        session.flush()
        session.add(
            LLMForecast(
                market_id=market.id,
                run_key="r",
                run_slot="2026-08-12:primary",
                model_provider="ollama",
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
                generated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            )
        )
        session.add(
            LLMForecast(
                market_id=market.id,
                run_key="r",
                run_slot="2026-08-12:primary",
                model_provider="openrouter",
                model_name="deepseek/deepseek-v4-flash-0731",
                prompt_version="fair_value_v0.1",
                generated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            )
        )

    with Session() as session:
        rows = list(session.scalars(select(LLMForecast).where(LLMForecast.market_id == market.id)))
    assert {r.model_provider for r in rows} == {"ollama", "openrouter"}
