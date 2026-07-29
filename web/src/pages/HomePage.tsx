import {
  ArrowRight,
  BarChart3,
  BookOpenCheck,
  Database,
  Gauge,
  GitCompareArrows,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Link } from "react-router-dom";
import { DataStamp } from "../components/DataStamp";
import { IndexHistoryChart } from "../components/Charts";
import { MetricCard } from "../components/MetricCard";
import { PremiumBadge } from "../components/PremiumBadge";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
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
      <section className="hero-section">
        <div className="hero-section__glow" aria-hidden="true" />
        <div className="shell-width hero-grid">
          <div className="hero-copy">
            <div className="eyebrow"><Sparkles size={15} /> Independent political market research</div>
            <h1>Where market prices end and partisan enthusiasm begins.</h1>
            <p>
              PPI compares political prediction-market probabilities with independently constructed fair values, preserving every public revision so performance can be judged after resolution.
            </p>
            <div className="hero-actions">
              <Link className="button button--primary" to="/markets">Explore markets <ArrowRight size={17} /></Link>
              <Link className="button button--secondary" to="/methodology">See how PPI works</Link>
            </div>
            <div className="hero-trust-row">
              <span><ShieldCheck size={15} /> Human-approved fair values</span>
              <span><Database size={15} /> Immutable public history</span>
              <span><Gauge size={15} /> Twice-daily observations</span>
            </div>
          </div>

          <aside className="hero-index-card">
            <div className="hero-index-card__header">
              <div>
                <span>Current PPI</span>
                <strong>Aggregate partisan premium</strong>
              </div>
              <StatusPill status={data.latest_run?.status ?? "NO_RUNS"} />
            </div>
            <div className="hero-index-card__value">
              {formatPremium(data.current_index.average_signed_premium)}
            </div>
            <p>
              Positive means tracked markets collectively price outcomes above PPI fair value. Negative means they price below it.
            </p>
            <div className="hero-index-card__split">
              <div><span>Absolute premium</span><strong>{formatPremiumMagnitude(data.current_index.average_absolute_premium)}</strong></div>
              <div><span>Above fair value</span><strong>{formatProbability(data.current_index.share_above_fair_value, 0)}</strong></div>
            </div>
            <DataStamp generatedAt={data.generated_at} />
          </aside>
        </div>
      </section>

      <section className="shell-width page-section page-section--pull-up">
        <div className="metrics-grid metrics-grid--four">
          <MetricCard label="Tracked markets" value={data.coverage.tracked_markets} detail={`${data.coverage.fresh_markets} currently fresh`} icon={BarChart3} />
          <MetricCard label="Published fair values" value={data.coverage.published_markets} detail={`${Math.round(publishedShare * 100)}% of tracked markets`} icon={BookOpenCheck} tone="accent" />
          <MetricCard label="Average absolute premium" value={formatPremiumMagnitude(data.current_index.average_absolute_premium)} detail="Magnitude of current disagreement" icon={GitCompareArrows} />
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
            <Link className="text-link" to="/methodology">Methodology <ArrowRight size={15} /></Link>
          </div>
          <IndexHistoryChart history={data.index_history} />
        </div>

        <div className="panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Largest dislocations</div>
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
                  <small>{market.region} · Market {formatProbability(market.market_probability)} · PPI {formatProbability(market.ppi_fair_value)}</small>
                </span>
                <PremiumBadge value={market.partisan_premium} />
              </Link>
            )) : (
              <EmptyState title="Fair values are being published" description="Dislocations appear here once at least one market has an approved PPI fair value." />
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
          <Link className="button button--secondary" to="/markets">View all markets <ArrowRight size={16} /></Link>
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
                  <span>{formatProbability(revision.previous_fair_value)}</span>
                  <ArrowRight size={16} />
                  <strong>{formatProbability(revision.fair_value)}</strong>
                </div>
                <p>{revision.thesis ?? "Published fair-value revision."}</p>
                <small>{formatShortDate(revision.published_at)}</small>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState title="No fair-value revisions yet" description="The first approved revisions will appear here without rewriting prior history." />
        )}
      </section>

      <section className="method-strip">
        <div className="shell-width method-strip__grid">
          <div>
            <div className="eyebrow">A deliberately constrained system</div>
            <h2>Automate collection. Preserve human judgment.</h2>
          </div>
          {[
            ["01", "Collect", "Prices, order books, approved feeds and public evidence are gathered on schedule."],
            ["02", "Construct", "Polling, forecasts, comparable markets, expert consensus and news form a proposal."],
            ["03", "Approve", "A human reviews the proposal before a new fair value can become public."],
            ["04", "Score", "After resolution, PPI and market probabilities are compared with Brier scores."],
          ].map(([number, title, copy]) => (
            <div className="method-step" key={number}>
              <span>{number}</span>
              <strong>{title}</strong>
              <p>{copy}</p>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
