from app.scoring.paper_trading import decide_paper_trade, mark_to_market, update_excursions
from app.scoring.ppi_score import PPIInputs, compute_ppi


def test_paper_trade_uses_executable_price_not_midpoint():
    inputs = PPIInputs(
        polymarket_yes=0.70,
        fair_yes=0.55,
        emotional_side="YES",
        spread=0.02,
        depth_3c=1000,
        resolution_risk=2,
        fair_value_confidence=0.90,
        identity_intensity=5,
        institutional_friction=5,
    )
    result = compute_ppi(inputs)
    decision = decide_paper_trade(result, inputs, yes_best_ask=0.70, no_best_ask=0.32, yes_best_bid=0.68)
    assert decision.create
    assert decision.side == "BUY_NO"
    assert decision.entry_price == 0.32


def test_paper_trade_does_not_create_if_simulated_size_below_10():
    inputs = PPIInputs(
        polymarket_yes=0.70,
        fair_yes=0.55,
        emotional_side="YES",
        spread=0.02,
        depth_3c=20,
        resolution_risk=2,
        fair_value_confidence=0.90,
        identity_intensity=5,
        institutional_friction=5,
    )
    result = compute_ppi(inputs)
    decision = decide_paper_trade(result, inputs, yes_best_ask=0.70, no_best_ask=0.32, yes_best_bid=0.68)
    assert not decision.create
    assert decision.reason == "simulated_size_below_10"


def test_mark_to_market_and_excursions():
    pnl = mark_to_market("BUY_YES", entry_price=0.50, size=100, current_yes_sell=0.60, current_no_sell=None)
    assert pnl == 20
    mfe, mae = update_excursions(pnl, None, None)
    assert mfe == 20
    assert mae == 20
    mfe, mae = update_excursions(-10, mfe, mae)
    assert mfe == 20
    assert mae == -10
