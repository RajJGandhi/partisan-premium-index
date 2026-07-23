from __future__ import annotations

from dataclasses import dataclass

from app.scoring.ppi_score import PPIInputs, PPIResult, should_create_paper_trade


@dataclass(frozen=True)
class PaperTradeDecision:
    create: bool
    side: str | None
    entry_price: float | None
    size: float
    reason: str | None


def executable_entry_price(
    side: str | None, yes_best_ask: float | None, no_best_ask: float | None, yes_best_bid: float | None = None
) -> float | None:
    if side == "BUY_YES":
        return yes_best_ask
    if side == "BUY_NO":
        if no_best_ask is not None:
            return no_best_ask
        if yes_best_bid is not None:
            return 1 - yes_best_bid
    return None


def simulated_size(default_notional: float, depth_3c: float | None) -> float:
    if depth_3c is None:
        return 0.0
    return min(default_notional, depth_3c * 0.25)


def decide_paper_trade(
    result: PPIResult,
    inputs: PPIInputs,
    yes_best_ask: float | None,
    no_best_ask: float | None,
    yes_best_bid: float | None = None,
    default_notional: float = 100.0,
) -> PaperTradeDecision:
    allowed, reason = should_create_paper_trade(result, inputs)
    if not allowed:
        return PaperTradeDecision(False, result.paper_side, None, 0.0, reason)
    entry = executable_entry_price(result.paper_side, yes_best_ask, no_best_ask, yes_best_bid)
    if entry is None:
        return PaperTradeDecision(False, result.paper_side, None, 0.0, "missing_executable_entry_price")
    size = simulated_size(default_notional, inputs.depth_3c)
    if size < 10:
        return PaperTradeDecision(False, result.paper_side, entry, size, "simulated_size_below_10")
    return PaperTradeDecision(True, result.paper_side, entry, size, None)


def mark_to_market(
    side: str, entry_price: float, size: float, current_yes_sell: float | None, current_no_sell: float | None
) -> float | None:
    if side == "BUY_YES":
        current = current_yes_sell
    elif side == "BUY_NO":
        current = current_no_sell
    else:
        return None
    if current is None:
        return None
    shares = size / entry_price if entry_price else 0.0
    return round((current - entry_price) * shares, 10)


def update_excursions(
    current_pnl: float, max_favorable: float | None, max_adverse: float | None
) -> tuple[float, float]:
    mfe = current_pnl if max_favorable is None else max(max_favorable, current_pnl)
    mae = current_pnl if max_adverse is None else min(max_adverse, current_pnl)
    return mfe, mae
