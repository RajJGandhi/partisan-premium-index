"""Lightweight idempotent migration runner for the existing local-first repository.

Fresh installations use SQLAlchemy metadata to create the full schema. Existing SQLite/PostgreSQL
installations receive additive columns required by the PPI product. Destructive changes are never
performed automatically; back up the database first.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.db import models  # noqa: F401
from app.db.database import Base, engine

ADDITIVE_COLUMNS = {
    "markets": {
        "tracking_id": "VARCHAR(64)",
        "yes_token_id": "TEXT",
        "no_token_id": "TEXT",
        "region": "VARCHAR(100)",
        "enabled": "BOOLEAN DEFAULT 0 NOT NULL",
        "status": "VARCHAR(50) DEFAULT 'TRACKED' NOT NULL",
        "source_pack_json": "TEXT",
        "preferred_domains_json": "TEXT",
        "current_thesis": "TEXT",
        "methodology_notes": "TEXT",
        "polling_weight": "FLOAT DEFAULT 0.35 NOT NULL",
        "forecast_weight": "FLOAT DEFAULT 0.25 NOT NULL",
        "comparable_weight": "FLOAT DEFAULT 0.20 NOT NULL",
        "expert_weight": "FLOAT DEFAULT 0.10 NOT NULL",
        "news_weight": "FLOAT DEFAULT 0.10 NOT NULL",
        "last_market_sync_at": "DATETIME",
        "last_evidence_sync_at": "DATETIME",
    },
    "market_snapshots": {
        "snapshot_date": "DATE",
        "snapshot_kind": "VARCHAR(30) DEFAULT 'intraday' NOT NULL",
        "last_trade_price": "FLOAT",
        "comparison_price": "FLOAT",
        "price_type": "VARCHAR(30)",
        "executable_buy_price": "FLOAT",
        "executable_sell_price": "FLOAT",
        "fair_value": "FLOAT",
        "partisan_premium": "FLOAT",
        "component_inputs_json": "TEXT",
        "component_weights_json": "TEXT",
        "effective_weights_json": "TEXT",
        "evidence_ids_json": "TEXT",
        "pending_proposal_ids_json": "TEXT",
        "freshness_status": "VARCHAR(30) DEFAULT 'UNKNOWN' NOT NULL",
        "pipeline_status": "VARCHAR(30) DEFAULT 'PENDING' NOT NULL",
        "status_message": "TEXT",
        "is_stale": "BOOLEAN DEFAULT 0 NOT NULL",
    },
    "fair_values": {
        "published_fair_yes": "FLOAT",
        "last_published_at": "DATETIME",
    },
    "job_runs": {
        "llm_forecasts_attempted": "INTEGER DEFAULT 0 NOT NULL",
        "llm_forecasts_succeeded": "INTEGER DEFAULT 0 NOT NULL",
        "llm_forecasts_abstained": "INTEGER DEFAULT 0 NOT NULL",
        "llm_forecasts_failed": "INTEGER DEFAULT 0 NOT NULL",
        "llm_forecasts_skipped": "INTEGER DEFAULT 0 NOT NULL",
        "evidence_classification_failed": "INTEGER DEFAULT 0 NOT NULL",
        "llm_fallback_count": "INTEGER DEFAULT 0 NOT NULL",
        "pipeline_mode": "VARCHAR(40) DEFAULT 'standard_mixed_fallback_allowed' NOT NULL",
        "run_classification": "VARCHAR(30) DEFAULT 'adhoc' NOT NULL",
        "superseded_by_id": "INTEGER",
    },
    "llm_forecasts": {
        "reviewed_status": "VARCHAR(30) DEFAULT 'UNREVIEWED' NOT NULL",
        "reviewed_by": "VARCHAR(100)",
        "reviewed_at": "DATETIME",
        "review_notes": "TEXT",
        "evidence_all_live_classified": "BOOLEAN",
        # PPI Quant v1.5: label the retained pre-rewrite blind-LLM series so it stays distinct from
        # the new deterministic Quant series and the new GPT/Claude blind benchmarks. Existing rows
        # backfill to the legacy labels via the column DEFAULT.
        "methodology_version": "VARCHAR(40) DEFAULT 'ppi-v0-legacy-blind-llm' NOT NULL",
        "forecast_role": "VARCHAR(30) DEFAULT 'legacy_blind_llm' NOT NULL",
    },
}

# PPI Quant v1.5 introduces entirely new tables (races, poll_observations, quant_forecasts,
# ensemble_forecasts, quant_evidence_bundles, data_provider_runs, provider_health, ...). These are
# defined as SQLAlchemy models in app/db/models_quant.py and created by the
# ``Base.metadata.create_all`` call at the top of migrate(); no hand-written DDL is needed for
# them here, and they touch nothing in the existing schema.


def _widen_llm_forecast_uniqueness(conn, inspector) -> None:
    """Widen llm_forecasts' uniqueness from (market_id, run_slot) to
    (market_id, run_slot, model_provider), so a second model series (e.g. openrouter) can never
    silently overwrite or be skipped in favor of the primary (ollama) series' row for the same
    market/slot. Nontrivial, dialect-specific, and only touches an existing 2-column constraint if
    one is actually still present -- idempotent, safe to run on every migrate() call.

    Back up the database before running this against production; see CLAUDE.md's migration rules.
    """
    if "llm_forecasts" not in inspector.get_table_names():
        return  # fresh install: create_all() above already created the 3-column constraint

    if conn.dialect.name == "postgresql":
        # ALTER TABLE ... DROP CONSTRAINT/DROP INDEX ... IF EXISTS are natively idempotent.
        conn.execute(text("ALTER TABLE llm_forecasts DROP CONSTRAINT IF EXISTS uq_llm_forecast_market_run_slot"))
        conn.execute(text("DROP INDEX IF EXISTS uq_llm_forecast_market_run_slot_idx"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_forecast_market_run_slot_provider_idx "
                "ON llm_forecasts(market_id, run_slot, model_provider)"
            )
        )
        return

    # SQLite cannot ALTER TABLE DROP a named UNIQUE constraint at all -- only a full table rebuild
    # removes one baked into the original CREATE TABLE. Detect whether the *old* 2-column-only
    # unique constraint is still present before doing this (idempotent: a DB already migrated, or
    # a fresh install, has no such 2-column-only unique index and this is a no-op).
    old_shape_present = any(
        set(idx["column_names"]) == {"market_id", "run_slot"} and idx.get("unique")
        for idx in inspector.get_indexes("llm_forecasts")
    ) or any(
        set(uc["column_names"]) == {"market_id", "run_slot"} for uc in inspector.get_unique_constraints("llm_forecasts")
    )
    if not old_shape_present:
        return

    existing_columns = [c["name"] for c in inspector.get_columns("llm_forecasts")]
    column_list = ", ".join(existing_columns)
    conn.execute(text("ALTER TABLE llm_forecasts RENAME TO llm_forecasts_pre_provider_migration"))
    Base.metadata.tables["llm_forecasts"].create(bind=conn)
    conn.execute(
        text(
            f"INSERT INTO llm_forecasts ({column_list}) "
            f"SELECT {column_list} FROM llm_forecasts_pre_provider_migration"
        )
    )
    conn.execute(text("DROP TABLE llm_forecasts_pre_provider_migration"))
    print("Rebuilt llm_forecasts (SQLite) with the widened (market_id, run_slot, model_provider) uniqueness.")


def migrate() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
        # Additive columns run *before* the uniqueness-widening rebuild below: SQLite's rebuild
        # copies every existing column via a raw INSERT SELECT (bypassing the ORM's Python-side
        # column defaults), so any NOT NULL column the new schema expects must already physically
        # exist -- with real, DDL-backfilled values -- on the old table first. ADD COLUMN ...
        # DEFAULT ... does backfill existing rows in both SQLite and Postgres; a Python-side
        # ``default=`` on the model does not apply to a raw INSERT at all.
        for table, columns in ADDITIVE_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    effective_ddl = ddl
                    if engine.dialect.name == "postgresql":
                        effective_ddl = effective_ddl.replace("DATETIME", "TIMESTAMP WITH TIME ZONE")
                        effective_ddl = effective_ddl.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {effective_ddl}"))
                    print(f"Added {table}.{name}")

        # Re-inspect via the same transactional connection (not a fresh engine-level connection,
        # which could see stale pre-transaction state) now that the ADD COLUMNs above have landed.
        inspector = inspect(conn)
        _widen_llm_forecast_uniqueness(conn, inspector)

        # Add indexes/uniqueness safely. NULL snapshot dates remain valid for legacy intraday rows.
        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_markets_enabled_active ON markets(enabled, active, closed)",
            "CREATE INDEX IF NOT EXISTS ix_market_snapshots_market_date ON market_snapshots(market_id, snapshot_date)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_markets_tracking_id_idx ON markets(tracking_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_daily_snapshot_idx ON market_snapshots(market_id, snapshot_date, snapshot_kind)",
            # NOT the old 2-column uq_llm_forecast_market_run_slot_idx -- superseded by
            # _widen_llm_forecast_uniqueness() above, which creates/maintains the 3-column
            # (market_id, run_slot, model_provider) index for both dialects.
            "CREATE INDEX IF NOT EXISTS ix_llm_forecasts_market_generated_idx ON llm_forecasts(market_id, generated_at)",
            "CREATE INDEX IF NOT EXISTS ix_llm_forecasts_reviewed_status_idx ON llm_forecasts(reviewed_status)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_blind_index_run_key_idx ON blind_index_runs(run_key)",
            "CREATE INDEX IF NOT EXISTS ix_job_runs_pipeline_mode_idx ON job_runs(pipeline_mode)",
            "CREATE INDEX IF NOT EXISTS ix_job_runs_run_classification_idx ON job_runs(run_classification)",
            "CREATE INDEX IF NOT EXISTS ix_job_runs_superseded_by_id_idx ON job_runs(superseded_by_id)",
        ]
        for stmt in index_statements:
            conn.execute(text(stmt))
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
