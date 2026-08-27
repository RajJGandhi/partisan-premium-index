from __future__ import annotations

from app.quant.config import (
    ENSEMBLE_METHODOLOGY_VERSION,
    METHODOLOGY_VERSION,
    PROVISIONAL_PARAMETERS,
    QUANT_V1,
    MethodologyConfig,
)


def test_version_identifiers():
    assert QUANT_V1.version == METHODOLOGY_VERSION == "ppi-quant-v1.0"
    assert ENSEMBLE_METHODOLOGY_VERSION == "ppi-ensemble-v1.5"


def test_ensemble_weights_sum_to_one():
    w = QUANT_V1.ensemble_weights
    assert set(w) == {"quant", "openai", "anthropic"}
    assert abs(sum(w.values()) - 1.0) < 1e-12
    assert w["quant"] == 0.60


def test_state_lean_weights_sum_to_one():
    assert abs(sum(QUANT_V1.state_lean_weights.values()) - 1.0) < 1e-12
    assert QUANT_V1.state_lean_weights["2024"] == 0.55


def test_config_hash_is_deterministic_and_stable():
    a = MethodologyConfig().config_hash()
    b = MethodologyConfig().config_hash()
    assert a == b == QUANT_V1.config_hash()
    assert len(a) == 64


def test_changing_a_constant_changes_the_hash():
    base = MethodologyConfig()
    tweaked = MethodologyConfig(poll_half_life_days=14.0)
    assert base.config_hash() != tweaked.config_hash()


def test_as_dict_round_trips_through_json():
    import json

    payload = json.dumps(QUANT_V1.as_dict(), sort_keys=True)
    assert "poll_half_life_days" in payload
    assert json.loads(payload)["version"] == "ppi-quant-v1.0"


def test_lookup_helpers_have_defined_fallbacks():
    assert QUANT_V1.population_weight(None) == 0.85
    assert QUANT_V1.population_weight("weird") == 0.85
    assert QUANT_V1.grade_weight("A+") == 1.10
    assert QUANT_V1.grade_weight(None) == 0.90
    assert QUANT_V1.sponsor_weight(partisan_sponsor=None, internal=False) == 1.00
    assert QUANT_V1.sponsor_weight(partisan_sponsor="PAC", internal=False) == 0.80
    assert QUANT_V1.sponsor_weight(partisan_sponsor=None, internal=True) == 0.75
    assert QUANT_V1.incumbency_bonus("governor") == 2.0
    assert QUANT_V1.incumbency_bonus("senate") == 1.5


def test_provisional_parameters_documented():
    assert len(PROVISIONAL_PARAMETERS) >= 15
    assert any("half_life" in p for p in PROVISIONAL_PARAMETERS)
