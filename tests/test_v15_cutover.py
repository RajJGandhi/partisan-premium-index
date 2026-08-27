from __future__ import annotations

from datetime import datetime, timezone

from app.db.models_quant import EnsembleForecast, QuantForecast, Race
from app.pipeline_v15 import cutover


def _reset_settings_cache():
    cutover.get_settings.cache_clear()


def test_default_headline_is_legacy(monkeypatch):
    monkeypatch.delenv("PPI_HEADLINE_SERIES", raising=False)
    _reset_settings_cache()
    try:
        assert cutover.headline_series() == "legacy_blind_llm"
    finally:
        _reset_settings_cache()


def test_env_can_select_quant(monkeypatch):
    monkeypatch.setenv("PPI_HEADLINE_SERIES", "quant")
    _reset_settings_cache()
    try:
        assert cutover.headline_series() == "quant"
    finally:
        monkeypatch.delenv("PPI_HEADLINE_SERIES", raising=False)
        _reset_settings_cache()


def test_invalid_env_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("PPI_HEADLINE_SERIES", "nonsense")
    _reset_settings_cache()
    try:
        assert cutover.headline_series() == "legacy_blind_llm"
    finally:
        monkeypatch.delenv("PPI_HEADLINE_SERIES", raising=False)
        _reset_settings_cache()


def test_headline_forecast_returns_none_for_legacy(quant_db, monkeypatch):
    monkeypatch.delenv("PPI_HEADLINE_SERIES", raising=False)
    _reset_settings_cache()
    try:
        with quant_db() as s:
            s.add(Race(race_id="r1", state="NC", office="senate", cycle=2026,
                       election_date=datetime(2026, 11, 3).date()))
            s.flush()
            assert cutover.headline_forecast(s, "r1") is None  # legacy path handles it, unchanged
    finally:
        _reset_settings_cache()


def test_headline_forecast_quant_and_ensemble(quant_db, monkeypatch):
    with quant_db() as s:
        s.add(Race(race_id="r1", state="NC", office="senate", cycle=2026,
                   election_date=datetime(2026, 11, 3).date()))
        s.add(QuantForecast(race_id="r1", run_key="k", methodology_version="ppi-quant-v1.0",
                            config_hash="c" * 64, input_hash="i" * 64,
                            generated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                            data_quality="STRONG", abstained=False, p_dem_win=0.77,
                            p_dem_win_uncapped=0.77, p_rep_win=0.23,
                            pipeline_mode="shadow", publication_status="SHADOW"))
        s.add(EnsembleForecast(race_id="r1", run_key="k", methodology_version="ppi-ensemble-v1.5",
                               available=True, ensemble_probability=0.79, weights_json="{}",
                               generated_at=datetime(2026, 8, 27, tzinfo=timezone.utc)))
        s.flush()

        monkeypatch.setenv("PPI_HEADLINE_SERIES", "quant")
        _reset_settings_cache()
        try:
            hf = cutover.headline_forecast(s, "r1")
            assert hf.series == "quant" and hf.probability == 0.77 and hf.is_shadow is True
            monkeypatch.setenv("PPI_HEADLINE_SERIES", "ensemble")
            _reset_settings_cache()
            hf2 = cutover.headline_forecast(s, "r1")
            assert hf2.series == "ensemble" and hf2.probability == 0.79
        finally:
            monkeypatch.delenv("PPI_HEADLINE_SERIES", raising=False)
            _reset_settings_cache()


def test_cutover_readiness_shape(quant_db):
    with quant_db() as s:
        rep = cutover.cutover_readiness(s)
        assert rep["current_headline_series"] == "legacy_blind_llm"
        assert len(rep["checklist"]) >= 5
        assert set(rep) >= {"quant_forecasts", "available_ensembles", "resolved_races", "note"}
