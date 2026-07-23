from sqlalchemy import create_engine, inspect

from app.db import models  # noqa: F401
from app.db.database import Base


def test_fresh_database_contains_required_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ppi.db'}")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    required = {
        "markets",
        "market_sources",
        "market_snapshots",
        "raw_market_responses",
        "evidence_items",
        "fair_value_components",
        "fair_value_proposals",
        "fair_value_revisions",
        "predictions",
        "market_resolutions",
        "daily_index",
        "job_runs",
        "source_runs",
        "admin_users",
    }
    assert required <= names
