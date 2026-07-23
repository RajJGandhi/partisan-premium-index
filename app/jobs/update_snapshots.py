from __future__ import annotations

import json

from app.db import crud
from app.db.database import get_session, init_db
from app.ingest.polymarket_clob import PolymarketCLOBClient, parse_clob_token_ids, summarize_yes_no_books


def run(limit: int | None = None) -> int:
    init_db()
    client = PolymarketCLOBClient()
    saved = 0
    with get_session() as session:
        markets = crud.list_active_markets(session, limit=limit)
        for market in markets:
            token_ids = parse_clob_token_ids(market.clob_token_ids_json)
            if not token_ids:
                continue
            yes_book = no_book = None
            try:
                yes_book = client.fetch_book(token_ids[0])
                if len(token_ids) > 1:
                    no_book = client.fetch_book(token_ids[1])
                summary = summarize_yes_no_books(yes_book, no_book)
                summary["volume"] = market.volume
                summary["liquidity"] = market.liquidity
                crud.create_snapshot(session, market.id, summary)
                saved += 1
            except Exception as exc:
                crud.log_raw_api_response(
                    session,
                    "polymarket_clob",
                    "/book",
                    {"market_id": market.id, "token_ids": token_ids},
                    {"yes": yes_book, "no": no_book},
                    None,
                    str(exc),
                )
    return saved


if __name__ == "__main__":
    count = run()
    print(f"Stored {count} order-book snapshots.")
