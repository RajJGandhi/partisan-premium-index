"""End-to-end: ingest -> quant -> blind -> ensemble -> resolutions -> score -> calibration,
and the two CLI scripts (spec sections 34-35, 47, 50 Phase I)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import database, models  # noqa: F401
    from app.db.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'eval.db'}", future=True)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    return engine


def _full_pipeline():
    import scripts.run_quant_shadow as shadow
    from app.db.database import get_session
    from app.providers.ingest import ingest_political_data
    from app.providers.offline import (
        offline_candidate_chain_factory,
        offline_election_history_chain,
        offline_generic_ballot_chain,
        offline_poll_chain,
    )

    races_path = SEED / "quant_example_races.json"
    doc = json.loads(races_path.read_text())
    configs = [
        {"race_id": r["race_id"], "state": r["state"], "office": r["office"], "cycle": int(r["cycle"]),
         "election_date": r["election_date"], "dem_candidate": r.get("dem_candidate"),
         "rep_candidate": r.get("rep_candidate")}
        for r in doc["races"]
    ]
    with get_session() as s:
        ingest_political_data(
            s, configs, cycle=2026,
            poll_chain=offline_poll_chain(races_path),
            generic_ballot_chain=offline_generic_ballot_chain(races_path),
            election_history_chain=offline_election_history_chain(),
            candidate_chain_factory=offline_candidate_chain_factory(races_path),
        )
    shadow.run(races_path, run_key="quant-shadow:2026-08-27:primary", dry_run=False, from_db=True, blind_mode="stub")


def test_score_and_calibration_end_to_end(isolated_db):
    from sqlalchemy import text

    from app.db.database import get_session
    from app.eval.calibration import build_calibration_report
    from app.eval.resolutions import load_resolutions_file
    from app.eval.scorer import score_all_resolved

    _full_pipeline()
    with get_session() as s:
        loaded = load_resolutions_file(s, SEED / "quant_example_resolutions.json")
        assert loaded == 3
        summary = score_all_resolved(s)
        assert set(summary) == {"xx-sen-2026", "yy-gov-2026", "zz-sen-2026"}
        assert all(v > 0 for v in summary.values())

        report = build_calibration_report(s, group_by=("series", "horizon_days"))
        assert report.n_resolved_races == 3
        series_scored = {g.key["series"] for g in report.groups}
        assert {"quant", "openai", "anthropic", "ensemble"} <= series_scored
        # every scored series has a Brier and direction-error rate
        assert report.overall.mean_brier is not None
        assert report.overall.direction_error_rate is not None
        # the 90-day horizon has no observation (all forecasts generated after election-90d) -> not scored
        assert all(g.key["horizon_days"] != 90 for g in report.groups)

    with isolated_db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM forecast_scores")).scalar() > 0
        # scoring never wrote to the legacy or market tables
        assert c.execute(text("SELECT COUNT(*) FROM llm_forecasts")).scalar() == 0


def test_scoring_is_idempotent(isolated_db):
    from app.db.database import get_session
    from app.db.models_quant import ForecastScore
    from app.eval.resolutions import load_resolutions_file
    from app.eval.scorer import score_all_resolved

    _full_pipeline()
    with get_session() as s:
        load_resolutions_file(s, SEED / "quant_example_resolutions.json")
        score_all_resolved(s)
    with get_session() as s:
        n1 = s.query(ForecastScore).count()
        briers1 = sorted((r.race_id, r.series, r.horizon_days, r.brier_score) for r in s.query(ForecastScore))
    with get_session() as s:
        score_all_resolved(s)
    with get_session() as s:
        assert s.query(ForecastScore).count() == n1
        briers2 = sorted((r.race_id, r.series, r.horizon_days, r.brier_score) for r in s.query(ForecastScore))
        assert briers1 == briers2


def test_backtest_cli_report(tmp_path):
    from app.eval.backtest import load_backtest_config, run_backtest

    races, cycle, _ = load_backtest_config(SEED / "quant_example_races.json")
    res = {r["race_id"]: r for r in json.loads((SEED / "quant_example_resolutions.json").read_text())["resolutions"]}
    report = run_backtest(races, cycle=cycle, resolutions=res, model_version="ppi-quant-v1.0")
    assert report.n_scored == 18
    d = report.as_dict()
    assert set(d["by_horizon"]) == {90, 60, 30, 14, 7, 1}
    assert all(d["by_horizon"][h]["mean_brier"] is not None for h in d["by_horizon"])
