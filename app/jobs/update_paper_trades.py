from __future__ import annotations

from sqlalchemy import desc, select

from app.db import crud
from app.db.database import get_session, init_db
from app.db.models import PPISignal, PaperTrade
from app.scoring.paper_trading import decide_paper_trade, mark_to_market, update_excursions
from app.scoring.ppi_score import PPIInputs


def run_create(limit: int | None = None) -> int:
    init_db()
    created = 0
    with get_session() as session:
        signals = list(session.scalars(select(PPISignal).order_by(desc(PPISignal.timestamp)).limit(limit or 100)))
        for signal in signals:
            existing = session.scalar(select(PaperTrade).where(PaperTrade.signal_id == signal.id))
            if existing:
                continue
            market = session.get(__import__("app.db.models", fromlist=["Market"]).Market, signal.market_id)
            snapshot = crud.latest_snapshot(session, signal.market_id)
            resolution = crud.latest_resolution_risk(session, signal.market_id)
            fv = crud.latest_fair_value(session, signal.market_id)
            if not snapshot or not fv:
                continue
            inputs = PPIInputs(
                polymarket_yes=signal.polymarket_yes,
                fair_yes=signal.fair_yes,
                spread=snapshot.spread,
                depth_3c=snapshot.depth_3c,
                resolution_risk=resolution.resolution_risk if resolution else 3,
                fair_value_confidence=fv.confidence or 0.0,
            )
            result = __import__("app.scoring.ppi_score", fromlist=["PPIResult"]).PPIResult(
                ppi_score=signal.ppi_score or 0,
                premium=signal.premium,
                action=signal.action or "ignore",
                paper_side=signal.paper_side,
                score_breakdown={},
                warnings=[],
            )
            decision = decide_paper_trade(
                result,
                inputs,
                yes_best_ask=snapshot.yes_best_ask,
                no_best_ask=snapshot.no_best_ask,
                yes_best_bid=snapshot.yes_best_bid,
            )
            if decision.create:
                crud.create_paper_trade(
                    session,
                    {
                        "signal_id": signal.id,
                        "market_id": signal.market_id,
                        "side": decision.side,
                        "entry_price": decision.entry_price,
                        "size": decision.size,
                        "current_price": decision.entry_price,
                        "current_pnl": 0.0,
                        "max_favorable_excursion": 0.0,
                        "max_adverse_excursion": 0.0,
                        "status": "open",
                        "notes": "Created by Reality Spread paper-trading rules.",
                    },
                )
                created += 1
    return created


def run_mark_to_market() -> int:
    init_db()
    updated = 0
    with get_session() as session:
        trades = list(session.scalars(select(PaperTrade).where(PaperTrade.status == "open")))
        for trade in trades:
            snapshot = crud.latest_snapshot(session, trade.market_id)
            if not snapshot:
                continue
            current_no_sell = snapshot.no_best_bid
            if current_no_sell is None and snapshot.yes_best_ask is not None:
                current_no_sell = 1 - snapshot.yes_best_ask
            pnl = mark_to_market(trade.side, trade.entry_price, trade.size, snapshot.yes_best_bid, current_no_sell)
            if pnl is None:
                continue
            current_price = snapshot.yes_best_bid if trade.side == "BUY_YES" else current_no_sell
            trade.current_price = current_price
            trade.current_pnl = pnl
            trade.max_favorable_excursion, trade.max_adverse_excursion = update_excursions(
                pnl, trade.max_favorable_excursion, trade.max_adverse_excursion
            )
            updated += 1
    return updated


def run() -> tuple[int, int]:
    created = run_create()
    updated = run_mark_to_market()
    return created, updated


if __name__ == "__main__":
    created, updated = run()
    print(f"Created {created} paper trades; updated {updated} open paper trades.")
