import {
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  Database,
  Gauge,
  GitCompareArrows,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { DataStamp } from "../components/DataStamp";
import { IndexHistoryChart } from "../components/Charts";
import { MetricCard } from "../components/MetricCard";
import { PremiumBadge } from "../components/PremiumBadge";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { usePublicData } from "../hooks/usePublicData";
import { formatPremium, formatPremiumMagnitude, formatProbability, formatShortDate } from "../lib/format";
import { publicData } from "../lib/data";

export function HomePage() {
  const { data, loading, error } = usePublicData(publicData.overview, []);

  if (loading) return <div className="shell-width page-space"><LoadingState /></div>;
  if (error || !data) return <div className="shell-width page-space"><ErrorState error={error ?? new Error("Overview unavailable")} /></div>;

  const publishedShare = data.coverage.tracked_markets
    ? data.coverage.published_markets / data.coverage.tracked_markets
    : 0;

  return (
    <>
      <section className="intro-strip">
        <div className="shell-width intro-grid">
          <div className="intro-copy">
            <div className="eyebrow">Independent political market research</div>
            <h1>PPI measures the gap between prediction-market prices and model-estimated political probabilities.</h1>
            <p>
              Large divergences may indicate sentiment, narrative effects, information asymmetry, or plain mispricing -- PPI makes that disagreement visible and keeps every revision on the public record.
            </p>
            <div className="intro-actions">
              <Link className="button button--primary" to="/markets">Explore markets <ArrowRight size={15} /></Link>
              <Link className="button button--secondary" to="/methodology">See how PPI works</Link>
            </div>
            <div className="trust-row">
              <span><ShieldCheck size={14} /> Blind-forecast fair values, published automatically</span>
              <span><Database size={14} /> Immutable public history</span>
              <span><Gauge size={14} /> Twice-daily observations</span>
            </div>
          </div>

          <div className="instrument-row">
            <div className="instrument">
              <span>Tracked markets</span>
              <strong className="num">{data.coverage.tracked_markets}</strong>
            </div>
            <div className="instrument">
              <span>Above fair value</span>
              <strong className="num">{formatProbability(data.current_index.share_above_fair_value, 0)}</strong>
            </div>
            <div className="instrument instrument--premium">
              <span>Aggregate premium</span>
              <strong className="num">{formatPremium(data.current_index.average_signed_premium)}</strong>
            </div>
          </div>
        </div>
        <div className="shell-width intro-stamp">
          <DataStamp generatedAt={data.generated_at} />
        </div>
      </section>

      <section className="shell-width page-section page-section--compact">
        <div className="metrics-grid metrics-grid--four">
          <MetricCard label="Tracked markets" value={data.coverage.tracked_markets} detail={`${data.coverage.fresh_markets} currently fresh`} icon={BarChart3} />
          <MetricCard label="Published fair values" value={data.coverage.published_markets} detail={`${Math.round(publishedShare * 100)}% of tracked markets`} icon={BookOpenCheck} tone="accent" />
          <MetricCard label="Avg. absolute premium" value={formatPremiumMagnitude(data.current_index.average_absolute_premium)} detail="Magnitude of current disagreement" icon={GitCompareArrows} />
          <MetricCard label="Resolved predictions" value={data.coverage.resolved_predictions} detail="Scored with Brier accuracy" icon={Gauge} />
        </div>
      </section>

      <section className="shell-width page-section two-column-section">
        <div className="panel panel--chart">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Index history</div>
              <h2>Is the market persistently above fair value?</h2>
              <p>Equal-weight average signed premium across currently published markets.</p>
            </div>
            <Link className="text-link" to="/methodology">Methodology <ArrowRight size={14} /></Link>
          </div>
          <IndexHistoryChart history={data.index_history} />
        </div>

        <div className="panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow eyebrow--premium">Largest dislocations</div>
              <h2>Where PPI disagrees most</h2>
              <p>Ranked by absolute difference between market price and fair value.</p>
            </div>
          </div>
          <div className="ranked-list">
            {data.largest_absolute_premiums.length ? data.largest_absolute_premiums.map((market, index) => (
              <Link className="ranked-list__item" to={`/markets/${market.slug}`} key={market.slug}>
                <span className="ranked-list__rank">{String(index + 1).padStart(2, "0")}</span>
                <span className="ranked-list__body">
                  <strong>{market.question}</strong>
                  <small>{market.region} · Market {formatProbability(market.market_probability)} · Model {formatProbability(market.ppi_fair_value)}</small>
                </span>
                <PremiumBadge value={market.partisan_premium} />
              </Link>
            )) : (
              <EmptyState title="Fair values are being published" description="Dislocations appear here once at least one market has a canonical forecast." />
            )}
          </div>
        </div>
      </section>

      <section className="shell-width page-section">
        <div className="section-heading section-heading--split">
          <div>
            <div className="eyebrow">Recent publications</div>
            <h2>Every fair-value change leaves a public trail.</h2>
            <p>Revisions preserve the previous value, new thesis, timestamp, and supporting evidence.</p>
          </div>
          <Link className="button button--secondary" to="/markets">View all markets <ArrowRight size={15} /></Link>
        </div>

        {data.recent_fair_value_revisions.length ? (
          <div className="revision-grid">
            {data.recent_fair_value_revisions.slice(0, 3).map((revision) => (
              <Link className="revision-card" to={`/markets/${revision.market_slug}`} key={`${revision.market_slug}-${revision.revision_number}`}>
                <div className="revision-card__topline">
                  <span>{revision.region}</span>
                  <span>Revision {revision.revision_number}</span>
                </div>
                <h3>{revision.question}</h3>
                <div className="revision-card__change">
                  <span className="num">{formatProbability(revision.previous_fair_value)}</span>
                  <ArrowRight size={15} />
                  <strong className="num">{formatProbability(revision.fair_value)}</strong>
                </div>
                <p>{revision.thesis ?? "Published fair-value revision."}</p>
                <small>{formatShortDate(revision.published_at)}</small>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState title="No fair-value revisions yet" description="The first published revisions will appear here without rewriting prior history." />
        )}
      </section>

      <section className="method-strip">
        <div className="shell-width method-strip__grid">
          <div>
            <div className="eyebrow">A deliberately constrained system</div>
            <h2>Automate collection. Preserve human judgment.</h2>
          </div>
          {[
            ["01", "Collect", "Prices, order books, and public evidence are gathered on schedule, never shown to the model."],
            ["02", "Estimate", "A blind LLM estimates a fair probability from evidence alone -- it never sees the market price."],
            ["03", "Publish", "A canonical forecast publishes automatically. Human review can only flag a genuine data-integrity concern -- it cannot approve or edit a forecast."],
            ["04", "Score", "After resolution, PPI and market probabilities are compared with Brier scores."],
          ].map(([number, title, copy]) => (
            <div className="method-step" key={number}>
              <span className="num">{number}</span>
              <strong>{title}</strong>
              <p>{copy}</p>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
