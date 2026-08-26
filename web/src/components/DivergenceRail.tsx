import { formatProbability } from "../lib/format";
import { PremiumBadge } from "./PremiumBadge";

interface DivergenceRailProps {
  market: number | null | undefined;
  model: number | null | undefined;
  premium: number | null | undefined;
  size?: "small" | "large";
  showScale?: boolean;
}

/**
 * The product's signature visualization: one 0-100 rail, a neutral marker for
 * the market price, a filled marker for the model's fair value, and the gap
 * between them physically rendered as the amber premium bar. Market and model
 * always keep the same left/right legend position regardless of which value
 * is larger, so the eye can scan a whole column of these without re-reading
 * labels; only the dots on the track move to the true values.
 */
export function DivergenceRail({ market, model, premium, size = "small", showScale = false }: DivergenceRailProps) {
  const hasBoth = market != null && model != null;
  const marketPct = market != null ? clamp(market * 100) : null;
  const modelPct = model != null ? clamp(model * 100) : null;
  const gapLeft = hasBoth ? Math.min(marketPct!, modelPct!) : null;
  const gapWidth = hasBoth ? Math.abs(marketPct! - modelPct!) : null;

  return (
    <div className={`divergence-rail${size === "large" ? " divergence-rail--large" : ""}`}>
      <div className="divergence-rail__legend">
        <div className="divergence-rail__figure">
          <span>
            <i className="divergence-rail__dot divergence-rail__dot--market" aria-hidden="true" />
            Market
          </span>
          <strong className="num">{formatProbability(market)}</strong>
        </div>
        <div className="divergence-rail__center">
          <span>Premium</span>
          <PremiumBadge value={premium} size={size === "large" ? "large" : "small"} />
        </div>
        <div className="divergence-rail__figure divergence-rail__figure--right">
          <span>
            Model
            <i className="divergence-rail__dot divergence-rail__dot--model" aria-hidden="true" />
          </span>
          <strong className="num">{formatProbability(model)}</strong>
        </div>
      </div>

      <div
        className="divergence-rail__track"
        role="img"
        aria-label={
          hasBoth
            ? `Market ${formatProbability(market)}, model ${formatProbability(model)}, premium ${formatProbability(Math.abs((market ?? 0) - (model ?? 0)))}`
            : "Awaiting model fair value"
        }
      >
        {hasBoth ? <div className="divergence-rail__gap" style={{ left: `${gapLeft}%`, width: `${gapWidth}%` }} /> : null}
        {modelPct != null ? <div className="divergence-rail__marker divergence-rail__marker--model" style={{ left: `${modelPct}%` }} /> : null}
        {marketPct != null ? <div className="divergence-rail__marker divergence-rail__marker--market" style={{ left: `${marketPct}%` }} /> : null}
      </div>

      {showScale ? (
        <div className="divergence-rail__scale">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      ) : null}
    </div>
  );
}

function clamp(value: number): number {
  return Math.min(100, Math.max(0, value));
}
