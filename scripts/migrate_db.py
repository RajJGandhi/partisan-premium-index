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
}


def migrate() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
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
        # Add indexes/uniqueness safely. NULL snapshot dates remain valid for legacy intraday rows.
        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_markets_enabled_active ON markets(enabled, active, closed)",
            "CREATE INDEX IF NOT EXISTS ix_market_snapshots_market_date ON market_snapshots(market_id, snapshot_date)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_markets_tracking_id_idx ON markets(tracking_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_market_daily_snapshot_idx ON market_snapshots(market_id, snapshot_date, snapshot_kind)",
        ]
        for stmt in index_statements:
            conn.execute(text(stmt))
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
