from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.database import get_session, init_db
from app.db.models import Market, MarketSnapshot
from app.ppi.polymarket import fetch_price_history

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill official Polymarket price history only")
    parser.add_argument("--tracking-id")
    parser.add_argument("--fidelity", type=int, default=1440, help="Minutes between points")
    args = parser.parse_args()
    init_db()
    inserted = 0
    with get_session() as session:
        stmt = select(Market).where(Market.enabled.is_(True))
        if args.tracking_id:
            stmt = stmt.where(Market.tracking_id == args.tracking_id)
        for market in session.scalars(stmt):
            if not market.yes_token_id:
                continue
            for point in fetch_price_history(market.yes_token_id, fidelity=args.fidelity):
                ts = datetime.fromtimestamp(int(point["t"]), tz=timezone.utc)
                exists = session.scalar(
                    select(MarketSnapshot.id).where(
                        MarketSnapshot.market_id == market.id,
                        MarketSnapshot.timestamp == ts,
                        MarketSnapshot.snapshot_kind == "market_price_only",
                    )
                )
                if exists:
                    continue
                price = float(point["p"])
                session.add(
                    MarketSnapshot(
                        market_id=market.id,
                        timestamp=ts,
                        snapshot_date=ts.date(),
                        snapshot_kind="market_price_only",
                        comparison_price=price,
                        yes_price_displayed=price,
                        no_price_displayed=1 - price,
                        price_type="historical_official",
                        freshness_status="HISTORICAL",
                        pipeline_status="MARKET_PRICE_ONLY",
                        status_message="Prelaunch historical record. No historical PPI fair value is implied.",
                    )
                )
                inserted += 1
    print(json.dumps({"inserted": inserted}, indent=2))
