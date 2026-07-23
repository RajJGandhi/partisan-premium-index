import pytest

from app.scoring.fair_value import compute_fair_value
from app.scoring.ppi_score import PPIInputs, compute_ppi


def test_all_inputs_weighted_fair_value_is_correct():
    result = compute_fair_value(
        polling_prob=0.50,
        forecast_prob=0.60,
        other_markets_prob=0.70,
        expert_prob=0.80,
        news_campaign_prob=0.90,
        confidence=0.80,
    )
    assert result.fair_yes == pytest.approx(0.635)
    assert result.adjusted_confidence == pytest.approx(0.80)


def test_manual_fair_yes_overrides_computed_value():
    result = compute_fair_value(
        polling_prob=0.10,
        forecast_prob=0.10,
        other_markets_prob=0.10,
        expert_prob=0.10,
        news_campaign_prob=0.10,
        manual_fair_yes=0.77,
        confidence=0.90,
    )
    assert result.fair_yes == 0.77
    assert result.used_manual is True


def test_missing_inputs_renormalize_weights():
    result = compute_fair_value(polling_prob=0.60, other_markets_prob=0.30, expert_prob=0.90, confidence=0.80)
    expected = (0.60 * (0.35 / 0.65)) + (0.30 * (0.20 / 0.65)) + (0.90 * (0.10 / 0.65))
    assert result.fair_yes == pytest.approx(expected)
    assert "forecast_prob" in result.missing_components


def test_confidence_penalty_affects_score():
    high = compute_ppi(
        PPIInputs(
            polymarket_yes=0.70,
            fair_yes=0.55,
            emotional_side="YES",
            fair_value_confidence=0.85,
            spread=0.02,
            depth_3c=1000,
            resolution_risk=2,
        )
    )
    low = compute_ppi(
        PPIInputs(
            polymarket_yes=0.70,
            fair_yes=0.55,
            emotional_side="YES",
            fair_value_confidence=0.50,
            spread=0.02,
            depth_3c=1000,
            resolution_risk=2,
        )
    )
    assert high.ppi_score > low.ppi_score
