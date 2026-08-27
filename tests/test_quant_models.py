from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.quant.append_only import (
    AppendOnlyViolation,
    assert_not_overwriting,
    correct_quant_forecast,
    flag_integrity,
    record_methodology_version,
    upsert_ensemble_forecast,
    upsert_quant_forecast,
)
from app.quant.config import QUANT_V1


def _qf(**kw):
    from app.db.models_quant import QuantForecast

    defaults = dict(
        race_id="nc-sen-2026",
        run_key="quant-shadow:2026-08-27:primary",
        methodology_version="ppi-quant-v1.0",
        config_hash="c" * 64,
        input_hash="i" * 64,
        generated_at=datetime(2026, 8, 27, 13, tzinfo=timezone.utc),
        data_quality="NORMAL",
        abstained=False,
        polling_margin=6.0,
        fundamental_margin=2.0,
        poll_weight=0.7,
        expected_margin=4.8,
        sigma_total=5.5,
        p_dem_win=0.808,
        p_dem_win_uncapped=0.808,
        p_rep_win=0.192,
        pipeline_mode="shadow",
        publication_status="SHADOW",
    )
    defaults.update(kw)
    return QuantForecast(**defaults)


def test_all_quant_tables_exist(quant_db):
    from sqlalchemy import inspect

    with quant_db() as s:
        names = set(inspect(s.bind).get_table_names())
    for t in (
        "races",
        "race_candidates",
        "poll_observations",
        "national_environment_observations",
        "historical_election_results",
        "candidate_status_snapshots",
        "data_provider_runs",
        "quant_evidence_bundles",
        "quant_forecasts",
        "ensemble_forecasts",
        "forecast_market_comparisons",
        "forecast_resolutions",
        "forecast_scores",
        "methodology_versions",
        "provider_health",
    ):
        assert t in names


def test_upsert_quant_forecast_is_idempotent(quant_db):
    with quant_db() as s:
        row, created = upsert_quant_forecast(s, _qf())
        s.commit()
        assert created and row.id is not None
        again, created2 = upsert_quant_forecast(s, _qf(p_dem_win=0.999))  # different number, same slot
        assert not created2
        assert again.id == row.id
        assert again.p_dem_win == 0.808  # original value preserved, NOT overwritten


def test_correction_appends_a_new_revision_without_editing_the_original(quant_db):
    with quant_db() as s:
        original, _ = upsert_quant_forecast(s, _qf())
        s.commit()
        corrected = correct_quant_forecast(
            s, original, _qf(p_dem_win=0.55, expected_margin=1.2), note="bad state-lean input; re-ingested"
        )
        s.commit()
        assert corrected.id != original.id
        assert corrected.revision == 1
        assert corrected.correction_of_id == original.id
        s.refresh(original)
        assert original.p_dem_win == 0.808  # untouched
        assert original.revision == 0


def test_flag_integrity_never_touches_the_numbers(quant_db):
    with quant_db() as s:
        row, _ = upsert_quant_forecast(s, _qf())
        s.commit()
        flag_integrity(s, row, note="duplicate poll double-counted")
        s.commit()
        s.refresh(row)
        assert row.integrity_flag == "FLAGGED"
        assert row.integrity_note == "duplicate poll double-counted"
        assert row.p_dem_win == 0.808 and row.expected_margin == 4.8


def test_assert_not_overwriting_guard():
    a = _qf()
    a.id = 1
    assert_not_overwriting(a, _qf())  # identical -> ok
    with pytest.raises(AppendOnlyViolation):
        assert_not_overwriting(a, _qf(expected_margin=9.9))


def test_methodology_version_is_write_once(quant_db):
    from app.db.models_quant import MethodologyVersion

    with quant_db() as s:
        record_methodology_version(
            s, version="ppi-quant-v1.0", kind="quant", config=QUANT_V1.as_dict(),
            config_hash=QUANT_V1.config_hash(), provisional=True, notes="first",
        )
        s.commit()
        record_methodology_version(
            s, version="ppi-quant-v1.0", kind="quant", config={"changed": True},
            config_hash="different", provisional=False, notes="second attempt",
        )
        s.commit()
        rows = s.query(MethodologyVersion).filter_by(version="ppi-quant-v1.0").all()
        assert len(rows) == 1
        assert rows[0].notes == "first"
        assert json.loads(rows[0].config_json)["version"] == "ppi-quant-v1.0"


def test_ensemble_upsert_idempotent_and_records_unavailability(quant_db):
    from app.db.models_quant import EnsembleForecast

    def _ef(**kw):
        d = dict(
            race_id="nc-sen-2026",
            run_key="quant-shadow:2026-08-27:primary",
            methodology_version="ppi-ensemble-v1.5",
            available=False,
            unavailable_reason="GPT/Claude not wired; NOT reweighted",
            weights_json=json.dumps({"quant": 0.6, "openai": 0.2, "anthropic": 0.2}),
            pipeline_mode="shadow",
            publication_status="SHADOW",
        )
        d.update(kw)
        return EnsembleForecast(**d)

    with quant_db() as s:
        row, created = upsert_ensemble_forecast(s, _ef())
        s.commit()
        assert created and not row.available
        _row2, created2 = upsert_ensemble_forecast(s, _ef(available=True, ensemble_probability=0.9))
        assert not created2  # slot already taken -> no silent overwrite
        s.refresh(row)
        assert row.available is False
