import { ArrowRight, Database, Droplets, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import { formatCompactNumber, formatProbability } from "../lib/format";
import type { MarketSummary } from "../lib/types";
import { PremiumBadge } from "./PremiumBadge";
import { StatusPill } from "./StatusPill";

export function MarketCard({ market }: { market: MarketSummary }) {
  return (
    <Link to={`/markets/${market.slug}`} className="market-card">
      <div className="market-card__topline">
        <div className="market-card__meta">
          <span><MapPin size={13} aria-hidden="true" />{market.region ?? "Global"}</span>
          <span>{market.category ?? "Political market"}</span>
        </div>
        <StatusPill status={market.is_stale ? "STALE" : market.freshness_status} compact />
      </div>

      <h3>{market.question ?? "Untitled market"}</h3>

      <div className="market-card__numbers">
        <div>
          <span>Market</span>
          <strong>{formatProbability(market.market_probability)}</strong>
        </div>
        <div>
          <span>PPI fair value</span>
          <strong>{formatProbability(market.ppi_fair_value)}</strong>
        </div>
      </div>

      <div className="market-card__premium">
        <PremiumBadge value={market.partisan_premium} />
      </div>

      <div className="market-card__footer">
        <span><Droplets size={14} aria-hidden="true" />{formatCompactNumber(market.liquidity)} liquidity</span>
        <span><Database size={14} aria-hidden="true" />{market.public_evidence_count} evidence</span>
        <ArrowRight className="market-card__arrow" size={17} aria-hidden="true" />
      </div>
    </Link>
  );
}
