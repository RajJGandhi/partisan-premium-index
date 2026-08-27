"""Shadow-mode runner writes the new Quant series without disturbing the headline series
(spec section 50, Phase D)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_quant_shadow as shadow

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "quant_example_races.json"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the app's engine/session at a throwaway migrated SQLite DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import (
        database,
        models,  # noqa: F401
    )
    from app.db.database import Base

    url = f"sqlite:///{tmp_path / 'shadow.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    return engine


def test_seed_file_is_valid_json_and_labelled_illustrative():
    doc = json.loads(SEED.read_text())
    assert "ILLUSTRATIVE" in doc["_disclaimer"].upper()
    assert len(doc["races"]) >= 3
    assert all(r["state"] in {"XX", "YY", "ZZ"} for r in doc["races"])  # not real states


def test_dry_run_writes_nothing(isolated_db):
    summary = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=True)
    assert summary["dry_run"] is True
    assert summary["written"] == 0
    from sqlalchemy import text

    with isolated_db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM quant_forecasts")).scalar() == 0


def test_shadow_run_writes_quant_series_and_is_idempotent(isolated_db):
    from sqlalchemy import text

    s1 = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False)
    assert s1["written"] == len(json.loads(SEED.read_text())["races"])

    s2 = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False)
    assert s2["written"] == 0
    assert s2["skipped_existing"] == s1["written"]

    with isolated_db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM quant_forecasts")).scalar() == s1["written"]
        assert c.execute(text("SELECT COUNT(*) FROM ensemble_forecasts")).scalar() == s1["written"]
        assert c.execute(text("SELECT COUNT(*) FROM methodology_versions")).scalar() == 1
        # ensemble rows are all explicitly unavailable (no silent reweight to Quant alone)
        avail = c.execute(text("SELECT DISTINCT available FROM ensemble_forecasts")).fetchall()
        assert {row[0] for row in avail} == {0}


def test_shadow_run_does_not_touch_headline_tables(isolated_db):
    from sqlalchemy import text

    shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False)
    with isolated_db.connect() as c:
        for table in ("llm_forecasts", "market_snapshots", "daily_index", "blind_index_runs"):
            assert c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0


def test_new_run_slot_appends_rather_than_overwrites(isolated_db):
    from sqlalchemy import text

    shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False)
    shadow.run(SEED, run_key="quant-shadow:2026-08-27:backup", dry_run=False)
    with isolated_db.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM quant_forecasts")).scalar()
        slots = c.execute(text("SELECT COUNT(DISTINCT run_key) FROM quant_forecasts")).scalar()
    assert slots == 2
    assert n == 2 * len(json.loads(SEED.read_text())["races"])


def test_degraded_race_in_seed_is_flagged_not_abstained(isolated_db):
    summary = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=True)
    zz = next(r for r in summary["races"] if r["race_id"] == "zz-sen-2026")
    assert zz["data_quality"] == "DEGRADED"
    assert zz["abstained"] is False


def test_shadow_from_db_after_offline_ingest(isolated_db):
    """providers -> DB -> engine: ingest offline, then run the Quant shadow from the DB tables."""
    from sqlalchemy import text

    from app.db.database import get_session
    from app.providers.ingest import ingest_political_data
    from app.providers.offline import (
        offline_candidate_chain_factory,
        offline_election_history_chain,
        offline_generic_ballot_chain,
        offline_poll_chain,
    )

    doc = json.loads(SEED.read_text())
    configs = [
        {
            "race_id": r["race_id"], "state": r["state"], "office": r["office"], "cycle": int(r["cycle"]),
            "election_date": r["election_date"], "dem_candidate": r.get("dem_candidate"),
            "rep_candidate": r.get("rep_candidate"),
        }
        for r in doc["races"]
    ]
    with get_session() as s:
        ingest_political_data(
            s, configs, cycle=2026,
            poll_chain=offline_poll_chain(SEED),
            generic_ballot_chain=offline_generic_ballot_chain(SEED),
            election_history_chain=offline_election_history_chain(),
            candidate_chain_factory=offline_candidate_chain_factory(SEED),
        )

    summary = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False, from_db=True)
    assert summary["source"] == "db"
    assert summary["written"] == 3
    with isolated_db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM quant_forecasts")).scalar() == 3
        assert c.execute(text("SELECT COUNT(*) FROM llm_forecasts")).scalar() == 0  # headline untouched
        qualities = {row[0] for row in c.execute(text("SELECT data_quality FROM quant_forecasts"))}
        assert qualities and qualities <= {"STRONG", "NORMAL", "THIN", "DEGRADED"}


def test_shadow_blind_stub_produces_ensemble(isolated_db):
    """quant -> evidence bundle -> blind stub -> real ensemble, all in shadow mode."""
    from sqlalchemy import text

    summary = shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False, blind_mode="stub")
    assert summary["blind_mode"] == "stub"
    assert summary["written"] == 3
    with isolated_db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM blind_benchmark_forecasts")).scalar() == 6  # 2 per race
        providers = {r[0] for r in c.execute(text("SELECT DISTINCT provider FROM blind_benchmark_forecasts"))}
        assert providers == {"openai", "anthropic"}
        assert {r[0] for r in c.execute(text("SELECT DISTINCT publication_status FROM blind_benchmark_forecasts"))} == {"STUB"}
        ens = list(c.execute(text("SELECT available, ensemble_probability FROM ensemble_forecasts")))
        assert len(ens) == 3
        assert all(row[0] == 1 and row[1] is not None for row in ens)  # all available (3 components present)
        assert c.execute(text("SELECT COUNT(*) FROM llm_forecasts")).scalar() == 0


def test_shadow_blind_live_without_keys_records_skipped(isolated_db):
    from sqlalchemy import text

    shadow.run(SEED, run_key="quant-shadow:2026-08-27:primary", dry_run=False, blind_mode="live")
    with isolated_db.connect() as c:
        statuses = {r[0] for r in c.execute(text("SELECT DISTINCT status FROM blind_benchmark_forecasts"))}
        assert statuses == {"SKIPPED_PROVIDER"}  # no keys -> explicit skip, never a fabricated value
        probs = list(c.execute(text("SELECT probability FROM blind_benchmark_forecasts")))
        assert all(p[0] is None for p in probs)
        ens_available = {r[0] for r in c.execute(text("SELECT available FROM ensemble_forecasts"))}
        assert ens_available == {0}  # unavailable, not silently reweighted to Quant
