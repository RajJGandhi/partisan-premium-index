from app.scoring.ppi_score import PPIInputs, compute_ppi, should_create_paper_trade


def test_high_premium_identity_low_spread_low_resolution_high_score():
    result = compute_ppi(
        PPIInputs(
            polymarket_yes=0.70,
            fair_yes=0.55,
            emotional_side="YES",
            identity_intensity=5,
            institutional_friction=4,
            deadline_decay_relevance=0,
            spread=0.02,
            depth_3c=1500,
            resolution_risk=2,
            fair_value_confidence=0.85,
        )
    )
    assert result.ppi_score >= 80
    assert result.paper_side == "BUY_NO"


def test_high_premium_resolution_risk_5_rejects_paper_trade():
    inputs = PPIInputs(
        polymarket_yes=0.80,
        fair_yes=0.55,
        emotional_side="YES",
        identity_intensity=5,
        institutional_friction=5,
        spread=0.02,
        depth_3c=1500,
        resolution_risk=5,
        fair_value_confidence=0.90,
    )
    result = compute_ppi(inputs)
    allowed, reason = should_create_paper_trade(result, inputs)
    assert not allowed
    assert reason == "resolution_risk_5"


def test_wide_spread_gets_penalty():
    tight = compute_ppi(
        PPIInputs(
            polymarket_yes=0.70,
            fair_yes=0.55,
            emotional_side="YES",
            spread=0.02,
            depth_3c=1000,
            resolution_risk=2,
            fair_value_confidence=0.80,
        )
    )
    wide = compute_ppi(
        PPIInputs(
            polymarket_yes=0.70,
            fair_yes=0.55,
            emotional_side="YES",
            spread=0.10,
            depth_3c=1000,
            resolution_risk=2,
            fair_value_confidence=0.80,
        )
    )
    assert tight.ppi_score > wide.ppi_score


def test_missing_fair_value_goes_to_research_queue():
    result = compute_ppi(PPIInputs(polymarket_yes=0.60, fair_yes=None))
    assert result.action == "research queue"
