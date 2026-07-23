import pytest

from app.ppi.methodology import compute_weighted_fair_value, partisan_premium, validate_weights


def test_default_weighted_fair_value_and_premium():
    result = compute_weighted_fair_value(
        {"polling": 0.50, "forecast": 0.60, "comparable": 0.70, "expert": 0.80, "news": 0.90}
    )
    assert result.fair_value == pytest.approx(0.635)
    assert partisan_premium(0.70, result.fair_value) == pytest.approx(0.065)


def test_missing_component_is_visible_and_redistributed():
    result = compute_weighted_fair_value(
        {"polling": 0.6, "forecast": None, "comparable": 0.4, "expert": 0.5, "news": 0.5}
    )
    assert "forecast" in result.missing_components
    assert result.needs_human_review is True
    assert sum(result.effective_weights.values()) == pytest.approx(1.0)


def test_weights_must_total_one():
    with pytest.raises(ValueError):
        validate_weights({"polling": 0.5, "forecast": 0.5, "comparable": 0.5, "expert": 0, "news": 0})
