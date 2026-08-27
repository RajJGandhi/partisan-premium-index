from __future__ import annotations

import pytest

from app.blind.ensemble_runner import compute_and_persist_ensemble
from app.db.models_quant import BlindBenchmarkForecast, EnsembleForecast, QuantForecast


def _qf(session, **kw):
    d = dict(
        race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
        methodology_version="ppi-quant-v1.0", config_hash="c" * 64, input_hash="i" * 64,
        data_quality="NORMAL", abstained=False, p_dem_win=0.90, p_dem_win_uncapped=0.90, p_rep_win=0.10,
        pipeline_mode="shadow", publication_status="SHADOW",
    )
    d.update(kw)
    row = QuantForecast(**d)
    session.add(row)
    session.flush()
    return row


def _blind(session, provider, prob, *, status="OK", stub=False):
    row = BlindBenchmarkForecast(
        race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary", provider=provider,
        model_name="m", prompt_version="blind_benchmark_v1", methodology_version="ppi-blind-v1",
        evidence_bundle_hash="e" * 64, status=status, probability=prob,
        publication_status="STUB" if stub else "SHADOW",
    )
    session.add(row)
    session.flush()
    return row


def test_all_three_present_gives_available_ensemble(quant_db):
    with quant_db() as s:
        q = _qf(s, p_dem_win=0.90)
        rows = [_blind(s, "openai", 0.80), _blind(s, "anthropic", 0.70)]
        ens, created = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
        assert created and ens.available
        assert ens.ensemble_probability == pytest.approx(0.60 * 0.90 + 0.20 * 0.80 + 0.20 * 0.70)
        assert ens.dispersion is not None and ens.max_pairwise_disagreement == pytest.approx(0.20)
        assert ens.robustness is None  # no market probability supplied


def test_missing_component_is_unavailable_no_reweight(quant_db):
    with quant_db() as s:
        q = _qf(s)
        rows = [_blind(s, "openai", 0.80)]  # anthropic missing
        ens, _ = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
        assert not ens.available
        assert ens.ensemble_probability is None
        assert "reweight" in ens.unavailable_reason.lower()
        assert ens.quant_probability == 0.90 and ens.openai_probability == 0.80  # still recorded
        assert ens.anthropic_probability is None


def test_abstained_benchmark_counts_as_missing(quant_db):
    with quant_db() as s:
        q = _qf(s)
        rows = [_blind(s, "openai", 0.80), _blind(s, "anthropic", 0.5, status="ABSTAINED")]
        ens, _ = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
        assert not ens.available
        assert ens.anthropic_probability is None


def test_quant_abstention_makes_ensemble_unavailable(quant_db):
    with quant_db() as s:
        q = _qf(s, abstained=True, data_quality="ABSTAIN", p_dem_win=None)
        rows = [_blind(s, "openai", 0.8), _blind(s, "anthropic", 0.7)]
        ens, _ = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
        assert not ens.available and ens.quant_probability is None


def test_market_probability_yields_robustness_band(quant_db):
    with quant_db() as s:
        q = _qf(s, p_dem_win=0.90)
        rows = [_blind(s, "openai", 0.89), _blind(s, "anthropic", 0.91)]  # tight cluster
        ens, _ = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows, market_probability=0.75,  # 15pt gap, ~2pt spread
        )
        s.commit()
        assert ens.robustness == "HIGH"


def test_idempotent_same_components(quant_db):
    Session = quant_db
    with Session() as s:
        q = _qf(s)
        rows = [_blind(s, "openai", 0.8), _blind(s, "anthropic", 0.7)]
        compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
    with Session() as s:
        q2 = s.query(QuantForecast).first()
        rows2 = s.query(BlindBenchmarkForecast).all()
        _ens, created = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q2, blind_rows=rows2,
        )
        s.commit()
        assert created is False
        assert s.query(EnsembleForecast).count() == 1


def test_stub_component_flags_ensemble_stub(quant_db):
    with quant_db() as s:
        q = _qf(s)
        rows = [_blind(s, "openai", 0.8, stub=True), _blind(s, "anthropic", 0.7, stub=True)]
        ens, _ = compute_and_persist_ensemble(
            s, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
            quant_forecast=q, blind_rows=rows,
        )
        s.commit()
        assert ens.publication_status == "STUB"
