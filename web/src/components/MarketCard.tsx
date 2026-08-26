import { ArrowRight, Database, Droplets, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import { formatCompactNumber } from "../lib/format";
import type { MarketSummary } from "../lib/types";
import { DivergenceRail } from "./DivergenceRail";
import { StatusPill } from "./StatusPill";

export function MarketCard({ market }: { market: MarketSummary }) {
  return (
    <Link to={`/markets/${market.slug}`} className="market-card">
      <div className="market-card__topline">
        <div className="market-card__meta">
          <span><MapPin size={12} aria-hidden="true" />{market.region ?? "Global"}</span>
          <span>{market.category ?? "Political market"}</span>
        </div>
        <StatusPill status={market.is_stale ? "STALE" : market.freshness_status} compact />
      </div>

      <h3>{market.question ?? "Untitled market"}</h3>

      <div className="market-card__rail">
        <DivergenceRail market={market.market_probability} model={market.ppi_fair_value} premium={market.partisan_premium} />
      </div>

      <div className="market-card__footer">
        <span><Droplets size={13} aria-hidden="true" /><span className="num">{formatCompactNumber(market.liquidity)}</span> liquidity</span>
        <span><Database size={13} aria-hidden="true" /><span className="num">{market.public_evidence_count}</span> evidence</span>
        <ArrowRight className="market-card__arrow" size={16} aria-hidden="true" />
      </div>
    </Link>
  );
}
