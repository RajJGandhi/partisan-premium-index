from __future__ import annotations

from app.db import crud
from app.db.database import get_session, init_db
from app.ingest.polymarket_gamma import PolymarketGammaClient, iter_relevant_market_payloads


def run(max_pages: int | None = None) -> int:
    init_db()
    client = PolymarketGammaClient()
    with get_session() as session:
        try:
            events = client.fetch_active_events(max_pages=max_pages)
            crud.log_raw_api_response(
                session, "polymarket_gamma", "/events", {"active": True, "closed": False}, events, 200
            )
        except Exception as exc:
            crud.log_raw_api_response(
                session, "polymarket_gamma", "/events", {"active": True, "closed": False}, None, None, str(exc)
            )
            raise
        count = 0
        for payload in iter_relevant_market_payloads(events):
            crud.upsert_market(session, payload)
            count += 1
        return count


if __name__ == "__main__":
    count = run()
    print(f"Stored/updated {count} relevant markets.")
