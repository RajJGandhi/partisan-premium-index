from __future__ import annotations

from app.quant.adapters import (
    CONTRACT_HOUSE_CONTROL,
    CONTRACT_SENATE_CONTROL,
    CONTRACT_STATEWIDE,
    HouseControlAdapter,
    SenateControlAdapter,
    StatewideRaceAdapter,
    UnsupportedAdapter,
    adapter_capabilities,
    get_adapter,
)
from app.quant.senate_control import SenateRaceDistribution


def test_get_adapter_dispatch():
    assert isinstance(get_adapter("statewide_race"), StatewideRaceAdapter)
    assert isinstance(get_adapter("senate_control"), SenateControlAdapter)
    assert isinstance(get_adapter("house_control"), HouseControlAdapter)
    assert isinstance(get_adapter("presidential_tweet_count"), UnsupportedAdapter)
    assert isinstance(get_adapter(""), UnsupportedAdapter)


def test_statewide_adapter_returns_a_quant_result(make_input, make_poll):
    inp = make_input(polls=[make_poll(pollster="A"), make_poll(pollster="B")], dem_incumbent=True)
    out = StatewideRaceAdapter().forecast(inp)
    assert out.contract_type == CONTRACT_STATEWIDE
    assert out.status == "SUPPORTED"
    assert out.quant_result is not None
    assert out.quant_result.p_dem_win is not None


def test_statewide_adapter_reports_abstention(make_input):
    inp = make_input(polls=(), candidate_mapping_confidence=0.3)
    out = StatewideRaceAdapter().forecast(inp)
    assert out.status == "ABSTAIN"
    assert out.quant_result.abstained
    assert out.reason


def test_senate_control_experimental_and_unavailable_when_empty():
    empty = SenateControlAdapter().forecast(
        [], holdover_dem=46, holdover_rep=46, tie_break_party="REP"
    )
    assert empty.status == "UNAVAILABLE"
    assert empty.senate_control_result is None

    ok = SenateControlAdapter().forecast(
        [SenateRaceDistribution("r1", 2.0, 5.0), SenateRaceDistribution("r2", -1.0, 5.0)],
        holdover_dem=46, holdover_rep=46, tie_break_party="REP", n_sims=2000, seed=1,
    )
    assert ok.status == "EXPERIMENTAL"
    assert ok.senate_control_result is not None
    assert 0.0 <= ok.senate_control_result.p_dem_control <= 1.0


def test_house_control_is_never_a_fabricated_number():
    out = HouseControlAdapter().forecast()
    assert out.status == "UNAVAILABLE"
    assert out.senate_control_result is None and out.quant_result is None
    assert "not implemented" in out.reason


def test_unsupported_abstains():
    out = UnsupportedAdapter().forecast()
    assert out.status == "ABSTAIN"
    assert "does not map" in out.reason


def test_capabilities_surface():
    caps = adapter_capabilities()
    assert caps[CONTRACT_STATEWIDE] == "SUPPORTED"
    assert caps[CONTRACT_SENATE_CONTROL] == "EXPERIMENTAL"
    assert caps[CONTRACT_HOUSE_CONTROL] == "UNAVAILABLE"
