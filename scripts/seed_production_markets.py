from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from app.db.database import get_session, init_db
from app.db.models import FairValueComponent, Market, MarketSource
from app.ingest.polymarket_gamma import parse_dt, safe_float


def as_bool(value, default=False):
    if pd.isna(value):
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}


def seed(demo: bool = False) -> dict:
    init_db()
    rows = pd.read_csv("data/seed/markets.csv").fillna("")
    packs = json.loads(Path("data/seed/source_packs.json").read_text(encoding="utf-8"))
    created = 0
    updated = 0
    with get_session() as session:
        for _, row in rows.iterrows():
            tracking_id = str(row["tracking_id"])
            market = session.scalar(select(Market).where(Market.tracking_id == tracking_id))
            if not market:
                market = Market(
                    tracking_id=tracking_id,
                    platform="polymarket",
                    platform_market_id=str(row["gamma_market_id"]),
                    question=str(row["question"]),
                )
                session.add(market)
                created += 1
            else:
                updated += 1
            market.event_id = str(row["event_id"] or "") or None
            market.condition_id = str(row["condition_id"] or "") or None
            market.slug = str(row["exact_polymarket_slug"] or "") or None
            description_text = str(row["description_text"] or "")
            rules_text = str(row["rules_text"] or "")
            market.description = description_text or None
            market.rules = rules_text or description_text or None
            market.resolution_source = str(row["resolution_source"] or "") or None
            market.end_date = parse_dt(row["end_date"])
            market.active = as_bool(row["active"], True)
            market.closed = as_bool(row["closed"], False)
            market.enabled = True
            market.status = "TRACKED"
            market.enable_order_book = as_bool(row["enable_order_book"], True)
            market.outcomes_json = str(row["outcomes_json"] or "") or None
            market.clob_token_ids_json = str(row["clob_token_ids_json"] or "") or None
            market.yes_token_id = str(row["yes_token_id"] or "") or None
            market.no_token_id = str(row["no_token_id"] or "") or None
            market.volume = safe_float(row["volume"])
            market.liquidity = safe_float(row["liquidity"])
            market.region = str(row["region"] or "") or None
            market.category = "politics"
            pack = packs.get(tracking_id, {})
            market.source_pack_json = json.dumps(pack, ensure_ascii=False)
            market.preferred_domains_json = json.dumps(pack.get("preferred_domains", []), ensure_ascii=False)
            session.flush()

            query = market.question
            existing = session.scalar(
                select(MarketSource).where(MarketSource.market_id == market.id, MarketSource.name == "Google News RSS")
            )
            if not existing:
                session.add(
                    MarketSource(
                        market_id=market.id,
                        source_type="google_news",
                        name="Google News RSS",
                        query=query,
                        config_json=json.dumps({"max_items": 10}),
                    )
                )
            existing_manual = session.scalar(
                select(MarketSource).where(
                    MarketSource.market_id == market.id, MarketSource.name == "Manual observations"
                )
            )
            if not existing_manual:
                session.add(MarketSource(market_id=market.id, source_type="manual", name="Manual observations"))
            existing_gdelt = session.scalar(
                select(MarketSource).where(MarketSource.market_id == market.id, MarketSource.name == "GDELT discovery")
            )
            if not existing_gdelt:
                session.add(
                    MarketSource(
                        market_id=market.id,
                        source_type="gdelt",
                        name="GDELT discovery",
                        query=query,
                        config_json=json.dumps({"max_items": 10}),
                    )
                )
            existing_external = session.scalar(
                select(MarketSource).where(
                    MarketSource.market_id == market.id,
                    MarketSource.name == "Manual external-market observations",
                )
            )
            if not existing_external:
                session.add(
                    MarketSource(
                        market_id=market.id,
                        source_type="external_market",
                        name="Manual external-market observations",
                    )
                )

            if demo:
                # Demonstration-only inputs; never created unless --demo is explicitly passed.
                demo_values = {"polling": 0.50, "forecast": 0.50, "comparable": 0.50, "expert": 0.50, "news": 0.50}
                weights = {"polling": 0.35, "forecast": 0.25, "comparable": 0.20, "expert": 0.10, "news": 0.10}
                for component, probability in demo_values.items():
                    fc = session.scalar(
                        select(FairValueComponent).where(
                            FairValueComponent.market_id == market.id, FairValueComponent.component_type == component
                        )
                    )
                    if not fc:
                        session.add(
                            FairValueComponent(
                                market_id=market.id,
                                component_type=component,
                                probability=probability,
                                weight=weights[component],
                                source_label="DEMONSTRATION DATA",
                                ingestion_method="demo_seed",
                                notes="Not live. Replace before publication.",
                            )
                        )
        return {"created": created, "updated": updated, "demo": demo, "total": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Add clearly labelled demonstration component inputs")
    args = parser.parse_args()
    print(json.dumps(seed(args.demo), indent=2))
