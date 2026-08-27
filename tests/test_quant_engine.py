from __future__ import annotations

from datetime import date

import pytest

from app.quant.engine import run_quant_forecast


def _polls(make_poll, n=4, dem=52, rep=44, newest=date(2026, 8, 22)):
    out = []
    for i in range(n):
        out.append(
            make_poll(
                pollster=f"Pollster {i}",
                end_date=date(newest.year, newest.month, newest.day - i * 3),
                dem_pct=dem,
                rep_pct=rep,
            )
        )
    return out


def test_end_to_end_produces_a_probability(make_input, make_poll):
    inp = make_input(polls=_polls(make_poll), dem_incumbent=True)
    r = run_quant_forecast(inp)
    assert not r.abstained
    assert r.data_quality in {"STRONG", "NORMAL"}
    assert 0.0 < r.p_dem_win < 1.0
    assert r.p_rep_win == pytest.approx(1.0 - r.p_dem_win)
    assert r.expected_margin is not None
    assert r.uncertainty.sigma_total > 0
    assert r.methodology_version == "ppi-quant-v1.0"
    assert len(r.config_hash) == 64 and len(r.input_hash) == 64


def test_input_hash_is_deterministic_and_input_sensitive(make_input, make_poll):
    a = make_input(polls=_polls(make_poll))
    b = make_input(polls=_polls(make_poll))
    assert a.input_hash() == b.input_hash()
    c = make_input(polls=_polls(make_poll, dem=55))
    assert c.input_hash() != a.input_hash()


def test_result_is_pure_modulo_timestamp(make_input, make_poll):
    inp = make_input(polls=_polls(make_poll), dem_incumbent=True)
    r1 = run_quant_forecast(inp)
    r2 = run_quant_forecast(inp)
    assert r1.p_dem_win == r2.p_dem_win
    assert r1.expected_margin == r2.expected_margin
    assert r1.uncertainty.sigma_total == r2.uncertainty.sigma_total


def test_no_polls_falls_back_to_fundamentals_only(make_input):
    inp = make_input(polls=(), rep_incumbent=True)
    r = run_quant_forecast(inp)
    assert not r.abstained
    assert r.poll_weight == 0.0
    assert r.polling_margin is None
    assert r.expected_margin == pytest.approx(r.fundamental_margin)
    assert r.data_quality == "THIN"


def test_abstains_when_mapping_confidence_below_threshold(make_input, make_poll):
    inp = make_input(polls=_polls(make_poll), candidate_mapping_confidence=0.4)
    r = run_quant_forecast(inp)
    assert r.abstained
    assert r.data_quality == "ABSTAIN"
    assert r.p_dem_win is None
    assert any("mapping confidence" in reason for reason in r.abstain_reasons)


def test_abstains_when_nothing_to_forecast(make_input):
    inp = make_input(polls=(), include_history=False, include_candidates=False)
    r = run_quant_forecast(inp)
    assert r.abstained
    assert r.p_dem_win is None


def test_degraded_quality_when_provider_degraded(make_input, make_poll):
    inp = make_input(polls=_polls(make_poll), provider_degraded=True)
    r = run_quant_forecast(inp)
    assert not r.abstained
    assert r.data_quality == "DEGRADED"


def test_stale_polls_downweight_alpha(make_input, make_poll):
    stale = [make_poll(pollster="Old", end_date=date(2026, 6, 1), dem_pct=60, rep_pct=40)]
    inp = make_input(polls=stale)
    r = run_quant_forecast(inp)
    # newest poll ~87 days old -> staleness x0.35 -> alpha well below the time cap
    assert r.poll_weight < 0.5
    assert r.data_quality == "THIN"


def test_market_dict_rejected_by_assert_market_free():
    from app.quant.types import assert_market_free

    with pytest.raises(ValueError):
        assert_market_free({"polls": [{"yes_best_bid": 0.55}]})
    with pytest.raises(ValueError):
        assert_market_free({"nested": {"deep": {"market_probability": 0.7}}})
    # a clean structure passes
    assert_market_free({"polls": [{"dem_pct": 52, "rep_pct": 44}], "state_lean": 3.0})
