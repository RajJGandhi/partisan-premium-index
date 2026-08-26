import {
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  CalendarClock,
  ExternalLink,
  FileCheck2,
  Gauge,
  Landmark,
  Layers3,
  Link2,
  Scale,
  ScrollText,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { ComponentChart, MarketHistoryChart } from "../components/Charts";
import { DataStamp } from "../components/DataStamp";
import { DivergenceRail } from "../components/DivergenceRail";
import { MetricCard } from "../components/MetricCard";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
import { usePublicData } from "../hooks/usePublicData";
import { publicData } from "../lib/data";
import {
  formatCompactNumber,
  formatDateTime,
  formatPremium,
  formatProbability,
  formatShortDate,
  labelize,
} from "../lib/format";

export function MarketDetailPage() {
  const { slug = "" } = useParams();
  const { data, loading, error } = usePublicData(() => publicData.market(slug), [slug]);

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading market profile…" /></div>;
  if (error || !data) return <div className="shell-width page-space"><ErrorState error={error ?? new Error("Market unavailable")} /></div>;

  const { market } = data;
  const spread = market.best_bid != null && market.best_ask != null ? market.best_ask - market.best_bid : market.spread;

  return (
    <div className="shell-width page-space">
      <Link className="back-link" to="/markets"><ArrowLeft size={15} /> Back to markets</Link>

      <section className="market-hero">
        <div className="market-hero__copy">
          <div className="market-hero__tags">
            <span>{market.region ?? "Global"}</span>
            <span>{market.category ?? "Political market"}</span>
            <StatusPill status={market.is_stale ? "STALE" : market.freshness_status} compact />
          </div>
          <h1>{market.question ?? "Untitled market"}</h1>
          <p>{market.description ?? "PPI tracks the market price, independently constructed fair value, and the resulting partisan premium through time."}</p>
          <div className="market-hero__actions">
            {market.market_url ? <a className="button button--secondary" href={market.market_url} target="_blank" rel="noreferrer">Open market <ExternalLink size={15} /></a> : null}
            <DataStamp generatedAt={data.generated_at} />
          </div>
        </div>
        <div className="market-hero__premium">
          <span>Market vs. model</span>
          <DivergenceRail market={market.market_probability} model={market.ppi_fair_value} premium={market.partisan_premium} size="large" showScale />
          <p>Market probability compared against the latest canonical model fair value, published automatically.</p>
        </div>
      </section>

      {market.forecast_status !== "OK" ? (
        <div className="notice notice--warning">
          <CalendarClock size={18} aria-hidden="true" />
          <div>
            {market.forecast_status === "ABSTAINED" ? (
              <>
                <strong>The model abstained on this market</strong>
                <span>The model was asked and declined to give a confident probability -- no value is invented or shown.</span>
              </>
            ) : market.forecast_status === "FLAGGED" ? (
              <>
                <strong>Forecast flagged for data-integrity review</strong>
                <span>A reviewer flagged a genuine concern with this forecast, so it is withheld from public display until resolved.</span>
              </>
            ) : market.forecast_status === "ERROR" ? (
              <>
                <strong>No usable forecast from the most recent canonical run</strong>
                <span>The most recent attempt did not produce a usable forecast. No fallback or substitute value is shown.</span>
              </>
            ) : (
              <>
                <strong>Independent fair value not yet available</strong>
                <span>The market is being observed, but no canonical model forecast has been generated yet. Price and evidence history continue to accumulate.</span>
              </>
            )}
          </div>
        </div>
      ) : null}

      <section className="metrics-grid metrics-grid--four page-section--compact">
        <MetricCard label="Market probability" value={formatProbability(market.market_probability)} detail={`${formatProbability(market.best_bid)} bid · ${formatProbability(market.best_ask)} ask`} icon={Landmark} />
        <MetricCard label="Model fair value" value={formatProbability(market.ppi_fair_value)} detail={`${market.revision_count} published revision${market.revision_count === 1 ? "" : "s"}`} icon={Scale} tone="accent" />
        <MetricCard label="Partisan premium" value={formatPremium(market.partisan_premium)} detail="Market minus model" icon={Gauge} tone={market.partisan_premium != null && market.partisan_premium !== 0 ? "positive" : "default"} />
        <MetricCard label="Market depth" value={formatCompactNumber(market.liquidity)} detail={`${formatProbability(spread)} quoted spread`} icon={Layers3} />
      </section>

      <section className="page-section detail-grid">
        <article className="panel panel--chart detail-grid__wide">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Probability history</div>
              <h2>Market price versus model fair value</h2>
              <p>Published observations only. Missing values are not imputed.</p>
            </div>
          </div>
          <MarketHistoryChart history={data.history} />
        </article>

        <article className="panel thesis-panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow eyebrow--premium">Current thesis</div>
              <h2>Why PPI differs</h2>
            </div>
          </div>
          {market.current_thesis ? <p className="thesis-copy">{market.current_thesis}</p> : <EmptyState title="No public thesis yet" description="A public thesis appears with the first published fair-value revision." />}
          <div className="thesis-panel__meta">
            <span><FileCheck2 size={14} /> {market.public_evidence_count} accepted evidence items</span>
            <span><CalendarClock size={14} /> Published {formatDateTime(market.last_fair_value_publication_at)}</span>
          </div>
        </article>
      </section>

      <section className="page-section detail-grid">
        <article className="panel panel--chart detail-grid__wide">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Fair-value construction</div>
              <h2>Five independent components</h2>
              <p>Component probabilities and configured weights are visible rather than hidden inside a single model output.</p>
            </div>
          </div>
          <ComponentChart components={data.components} />
          {data.components.length ? (
            <div className="component-list">
              {data.components.map((component) => (
                <div className="component-row" key={component.type}>
                  <div>
                    <strong>{labelize(component.type)}</strong>
                    <span>{component.source_label ?? "Public component input"}</span>
                  </div>
                  <div><span>Weight</span><strong className="num">{formatProbability(component.weight, 0)}</strong></div>
                  <div><span>Value</span><strong className="num">{formatProbability(component.probability)}</strong></div>
                  {component.source_url ? <a href={component.source_url} target="_blank" rel="noreferrer" aria-label={`Open ${component.type} source`}><ExternalLink size={15} /></a> : <span />}
                </div>
              ))}
            </div>
          ) : null}
        </article>

        <article className="panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Market data</div>
              <h2>Latest observation</h2>
            </div>
          </div>
          <dl className="definition-list">
            <div><dt>Observed</dt><dd>{formatDateTime(market.last_observed_at)}</dd></div>
            <div><dt>Price type</dt><dd>{labelize(market.price_type)}</dd></div>
            <div><dt>Best bid</dt><dd>{formatProbability(market.best_bid)}</dd></div>
            <div><dt>Best ask</dt><dd>{formatProbability(market.best_ask)}</dd></div>
            <div><dt>Spread</dt><dd>{formatProbability(spread)}</dd></div>
            <div><dt>Volume</dt><dd>{formatCompactNumber(market.volume)}</dd></div>
            <div><dt>Liquidity</dt><dd>{formatCompactNumber(market.liquidity)}</dd></div>
            <div><dt>Pipeline</dt><dd><StatusPill status={market.pipeline_status} compact /></dd></div>
          </dl>
        </article>
      </section>

      <section className="page-section two-column-section two-column-section--balanced">
        <article className="panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Accepted evidence</div>
              <h2>What informed the public record</h2>
              <p>Only evidence approved for public display is included.</p>
            </div>
          </div>
          {data.evidence.length ? (
            <div className="evidence-list">
              {data.evidence.map((item, index) => (
                <a className="evidence-item" href={item.url} target="_blank" rel="noreferrer" key={`${item.url}-${index}`}>
                  <div className="evidence-item__topline">
                    <span>{item.source_name ?? labelize(item.source_type)}</span>
                    <span>{formatShortDate(item.published_at ?? item.discovered_at)}</span>
                  </div>
                  <strong>{item.title ?? "Public evidence"}</strong>
                  {item.summary ? <p>{item.summary}</p> : null}
                  <div className="evidence-item__tags">
                    {item.category ? <span>{labelize(item.category)}</span> : null}
                    {item.direction ? <span>Direction: {labelize(item.direction)}</span> : null}
                    {item.relevance_score != null ? <span>Relevance {formatProbability(item.relevance_score, 0)}</span> : null}
                  </div>
                  <ExternalLink size={14} className="evidence-item__icon" />
                </a>
              ))}
            </div>
          ) : <EmptyState title="No public evidence yet" description="Approved evidence will appear here after review." />}
        </article>

        <article className="panel">
          <div className="panel__header">
            <div>
              <div className="eyebrow">Revision history</div>
              <h2>Published values are append-only</h2>
              <p>Corrections are identified rather than silently replacing prior work.</p>
            </div>
          </div>
          {data.revisions.length ? (
            <div className="timeline">
              {[...data.revisions].reverse().map((revision) => (
                <div className="timeline__item" key={revision.revision_number}>
                  <span className="timeline__dot" />
                  <div className="timeline__topline">
                    <strong>Revision {revision.revision_number}</strong>
                    <span>{formatShortDate(revision.published_at)}</span>
                  </div>
                  <div className="timeline__change">
                    <span className="num">{formatProbability(revision.previous_fair_value)}</span> <ArrowRight size={13} /> <strong className="num">{formatProbability(revision.fair_value)}</strong>
                    {revision.is_correction ? <span className="mini-label">Correction</span> : null}
                  </div>
                  {revision.thesis ? <p>{revision.thesis}</p> : null}
                </div>
              ))}
            </div>
          ) : <EmptyState title="No revisions published" description="The first published fair value will begin the permanent revision record." />}
        </article>
      </section>

      <section className="page-section two-column-section two-column-section--balanced">
        <article className="panel">
          <div className="panel__header">
            <div><div className="eyebrow">Resolution framework</div><h2>What exactly resolves this market</h2></div>
          </div>
          <div className="prose-block">
            <h3><ScrollText size={16} /> Rules</h3>
            <p>{market.rules ?? "The official market rules and qualifying resolution source govern the outcome."}</p>
            <h3><BookOpenText size={16} /> Resolution source</h3>
            <p>{market.resolution_source ?? "Official election results and the market's published resolution criteria."}</p>
            {data.resolution ? (
              <div className="resolution-box">
                <StatusPill status="RESOLVED" />
                <strong>{data.resolution.label ?? formatProbability(data.resolution.outcome)}</strong>
                <span>{formatDateTime(data.resolution.resolved_at)}</span>
                {data.resolution.source_url ? <a href={data.resolution.source_url} target="_blank" rel="noreferrer">Resolution source <ExternalLink size={13} /></a> : null}
              </div>
            ) : null}
          </div>
        </article>

        <article className="panel">
          <div className="panel__header">
            <div><div className="eyebrow">Public sources</div><h2>Configured research inputs</h2></div>
          </div>
          {data.sources.length ? (
            <div className="source-list">
              {data.sources.map((source, index) => (
                <div className="source-list__item" key={`${source.name}-${index}`}>
                  <span className="source-list__icon"><Link2 size={15} /></span>
                  <div><strong>{source.name ?? labelize(source.type)}</strong><span>{labelize(source.type)}</span></div>
                  {source.url ? <a href={source.url} target="_blank" rel="noreferrer"><ExternalLink size={15} /></a> : null}
                </div>
              ))}
            </div>
          ) : <EmptyState title="No public source list" description="Source metadata will appear as the market's research pack is published." />}
        </article>
      </section>
    </div>
  );
}
