from __future__ import annotations

import json
from pathlib import Path

from app.db.models_quant import (
    BlindBenchmarkForecast,
    EnsembleForecast,
    QuantForecast,
)
from app.pipeline_v15.orchestrator import run_v15_pipeline

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "quant_example_races.json"
RK = "ppi-v15:2026-08-27:primary"


def _configs():
    doc = json.loads(SEED.read_text())
    return [
        {"race_id": r["race_id"], "state": r["state"], "office": r["office"], "cycle": int(r["cycle"]),
         "election_date": r["election_date"], "dem_candidate": r.get("dem_candidate"),
         "rep_candidate": r.get("rep_candidate"), "source": "seed"}
        for r in doc["races"]
    ]


def _offline_kwargs():
    from app.providers.offline import (
        offline_candidate_chain_factory,
        offline_election_history_chain,
        offline_generic_ballot_chain,
        offline_poll_chain,
    )

    return dict(
        poll_chain=offline_poll_chain(SEED),
        generic_ballot_chain=offline_generic_ballot_chain(SEED),
        election_history_chain=offline_election_history_chain(),
        candidate_chain_factory=offline_candidate_chain_factory(SEED),
    )


def test_runs_all_ten_stages_offline(quant_db):
    with quant_db() as s:
        summary = run_v15_pipeline(
            s, race_configs=_configs(), run_key=RK, blind_mode="stub",
            ingest_kwargs=_offline_kwargs(),
        )
        s.commit()
        assert summary.status == "OK"
        assert summary.headline_series == "legacy_blind_llm"  # NOT flipped
        assert set(summary.stages) >= {"1_discover", "2_market_snapshot", "3_political_data",
                                       "4_validate", "5_9_forecasts", "10_publish"}
        assert summary.stages["1_discover"] == {"skipped": "no discovery provider supplied"}
        assert summary.stages["5_9_forecasts"] == {"ok": 3, "abstained": 0, "errors": 0}
        assert all(r.status == "OK" and r.ensemble_available for r in summary.races)
        assert s.query(QuantForecast).count() == 3
        assert s.query(BlindBenchmarkForecast).count() == 6
        assert s.query(EnsembleForecast).count() == 3


def test_job_run_recorded(quant_db):
    from app.db.models import JobRun

    with quant_db() as s:
        summary = run_v15_pipeline(s, race_configs=_configs(), run_key=RK, blind_mode="stub",
                                   ingest_kwargs=_offline_kwargs())
        s.commit()
        job = s.query(JobRun).filter_by(run_key=RK).one()
        assert job.job_name == "ppi-v15-daily"
        assert job.status == "OK"
        assert job.markets_attempted == 3 and job.markets_succeeded == 3
        assert job.id == summary.job_run_id


def test_idempotent_per_run_key(quant_db):
    session_factory = quant_db
    for _ in range(3):
        with session_factory() as s:
            run_v15_pipeline(s, race_configs=_configs(), run_key=RK, blind_mode="stub",
                             ingest_kwargs=_offline_kwargs())
            s.commit()
    with session_factory() as s:
        assert s.query(QuantForecast).count() == 3
        assert s.query(BlindBenchmarkForecast).count() == 6
        assert s.query(EnsembleForecast).count() == 3
        from app.db.models import JobRun

        assert s.query(JobRun).filter_by(run_key=RK).count() == 1


def test_one_bad_race_does_not_abort_the_others(quant_db):
    configs = _configs() + [
        {"race_id": "zz-nodata-2026", "state": "ZZ", "office": "senate", "cycle": 2026,
         "election_date": "2026-11-03", "source": "seed"}
    ]
    # the offline chains only know the 3 seed races, so zz-nodata has no ingested polls/history
    with quant_db() as s:
        summary = run_v15_pipeline(s, race_configs=configs, run_key=RK, blind_mode="stub",
                                   ingest_kwargs=_offline_kwargs())
        s.commit()
        by_id = {r.race_id: r for r in summary.races}
        assert by_id["xx-sen-2026"].status == "OK"
        assert by_id["zz-nodata-2026"].status in {"ERROR", "ABSTAIN"}
        assert summary.status in {"OK", "PARTIAL"}
        # the good races still got persisted
        assert s.query(QuantForecast).filter_by(race_id="xx-sen-2026").count() == 1


def test_blind_disabled_records_skipped_and_unavailable_ensemble(quant_db):
    with quant_db() as s:
        summary = run_v15_pipeline(s, race_configs=_configs(), run_key=RK, blind_mode="live",
                                   ingest_kwargs=_offline_kwargs())
        s.commit()
        assert {st for r in summary.races for st in r.blind_statuses.values()} == {"SKIPPED_PROVIDER"}
        assert all(not r.ensemble_available for r in summary.races)  # never reweighted to Quant alone
