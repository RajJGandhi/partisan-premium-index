import { Activity, Clock3, Database, FileCheck2, ShieldCheck, TriangleAlert } from "lucide-react";
import { DataStamp } from "../components/DataStamp";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
import { usePublicData } from "../hooks/usePublicData";
import { publicData } from "../lib/data";
import { formatDateTime, formatDuration } from "../lib/format";
import { v15Data } from "../lib/v15Data";

export function SystemStatusPage() {
  const { data, loading, error } = usePublicData(publicData.systemStatus, []);
  const v15 = usePublicData(v15Data.providerStatus, []);

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading system status…" /></div>;
  if (error || !data) return <div className="shell-width page-space"><ErrorState error={error ?? new Error("System status unavailable")} /></div>;

  const latest = data.latest_run;
  const health = data.run_health;
  const successRate = latest?.markets_attempted
    ? latest.markets_succeeded / latest.markets_attempted
    : null;
  const staleSuccess =
    health?.hours_since_canonical_success != null && health.hours_since_canonical_success > 18;

  return (
    <div className="shell-width page-space">
      <PageHeader
        eyebrow="System status"
        title="The public record includes its own operating health."
        description="Recent pipeline runs, market freshness and source outcomes are published without exposing internal errors, credentials or administrative data."
        actions={<DataStamp generatedAt={data.generated_at} />}
      />

      <section className={`status-banner status-banner--${data.status.toLowerCase()}`}>
        <span className="status-banner__icon"><Activity size={24} /></span>
        <div><strong>Current system status: {data.status}</strong><span>{latest ? `Latest run finished ${formatDateTime(latest.finished_at)} with ${latest.error_count} errors.` : "No production run has been published yet."}</span></div>
        <StatusPill status={data.status} />
      </section>

      <section className="metrics-grid metrics-grid--four page-section--compact">
        <MetricCard
          label="Last successful canonical run"
          value={health?.last_canonical_success ? formatDateTime(health.last_canonical_success) : "None yet"}
          detail={
            health?.last_canonical_success
              ? `${health.markets_completed} markets · ${health.hours_since_canonical_success ?? "?"}h ago`
              : "No canonical run has completed"
          }
          icon={Clock3}
          tone={staleSuccess ? "negative" : "accent"}
        />
        <MetricCard
          label="Last attempt"
          value={health?.last_status ?? "NO_RUNS"}
          detail={
            health?.last_attempt
              ? `${formatDateTime(health.last_attempt)}${health.last_error_stage ? ` · failed at ${health.last_error_stage}` : ""}`
              : "No attempt recorded"
          }
          icon={Activity}
          tone={health && health.last_status !== "OK" ? "negative" : "default"}
        />
        <MetricCard
          label="Consecutive failed attempts"
          value={health?.consecutive_failed_attempts ?? 0}
          detail={health?.consecutive_failed_attempts ? "Every attempt is recorded, even pre-pipeline failures" : "Healthy"}
          icon={TriangleAlert}
          tone={health?.consecutive_failed_attempts ? "negative" : "default"}
        />
        <MetricCard label="Snapshots written" value={latest?.snapshots_written ?? 0} detail={`${latest?.evidence_relevant ?? 0} relevant evidence items`} icon={FileCheck2} />
      </section>

      <section className="metrics-grid metrics-grid--four page-section--compact">
        <MetricCard label="Fresh markets" value={`${data.summary.fresh_markets}/${data.summary.tracked_markets}`} detail={successRate == null ? "No run data" : `${Math.round(successRate * 100)}% latest run success`} icon={Database} tone="accent" />
        <MetricCard label="Stale markets" value={data.summary.stale_markets} detail="Require a new valid observation" icon={Clock3} tone={data.summary.stale_markets ? "negative" : "default"} />
        <MetricCard label="Source failures" value={data.summary.latest_source_failures} detail="In the latest published run" icon={TriangleAlert} tone={data.summary.latest_source_failures ? "negative" : "default"} />
        <MetricCard label="Errors (latest run)" value={latest?.error_count ?? 0} detail={latest?.error_stage ? `Stage: ${latest.error_stage}` : "No error stage recorded"} icon={FileCheck2} tone={latest?.error_count ? "negative" : "default"} />
      </section>

      <section className="page-section two-column-section two-column-section--balanced">
        <article className="panel">
          <div className="panel__header"><div><div className="eyebrow">Latest run</div><h2>Pipeline summary</h2></div></div>
          {latest ? (
            <dl className="definition-list definition-list--large">
              <div><dt>Status</dt><dd><StatusPill status={latest.status} compact /></dd></div>
              <div><dt>Trigger</dt><dd>{latest.trigger_type}</dd></div>
              <div><dt>Started</dt><dd>{formatDateTime(latest.started_at)}</dd></div>
              <div><dt>Duration</dt><dd>{formatDuration(latest.started_at, latest.finished_at)}</dd></div>
              <div><dt>Markets</dt><dd>{latest.markets_succeeded}/{latest.markets_attempted}</dd></div>
              <div><dt>Evidence discovered</dt><dd>{latest.evidence_discovered}</dd></div>
              <div><dt>Evidence relevant</dt><dd>{latest.evidence_relevant}</dd></div>
              <div><dt>Proposals created</dt><dd>{latest.proposals_created}</dd></div>
              <div><dt>Errors</dt><dd>{latest.error_count}</dd></div>
            </dl>
          ) : <EmptyState title="No production run yet" description="The first successful scheduled workflow will populate this summary." />}
        </article>

        <article className="panel security-panel">
          <div className="panel__header"><div><div className="eyebrow">Public architecture</div><h2>Static by design</h2></div></div>
          <ShieldCheck size={34} />
          <p>Visitors receive pre-generated HTML, JavaScript and sanitized JSON. Public traffic cannot trigger Python, query Supabase, run an LLM or access administration.</p>
          <ul>
            <li>No public database credentials</li>
            <li>No public write endpoint</li>
            <li>No public admin route</li>
            <li>No per-request server computation</li>
          </ul>
        </article>
      </section>

      <section className="panel page-section">
        <div className="panel__header"><div><div className="eyebrow">Recent runs</div><h2>Automation history</h2><p>The latest published execution records, newest first.</p></div></div>
        {data.recent_runs.length ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Run</th><th>Status</th><th>Failed stage</th><th>Finished</th><th>Duration</th><th>Markets</th><th>Evidence</th><th>Snapshots</th><th>Errors</th></tr></thead>
              <tbody>{data.recent_runs.map((run) => (
                <tr key={run.run_key}>
                  <td><strong>{run.trigger_type}</strong><span>{run.run_key}</span></td>
                  <td><StatusPill status={run.status} compact /></td>
                  <td>{run.error_stage ?? "—"}</td>
                  <td>{formatDateTime(run.finished_at)}</td>
                  <td>{formatDuration(run.started_at, run.finished_at)}</td>
                  <td>{run.markets_succeeded}/{run.markets_attempted}</td>
                  <td>{run.evidence_relevant}/{run.evidence_discovered}</td>
                  <td>{run.snapshots_written}</td>
                  <td>{run.error_count}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <EmptyState title="No recent runs" description="Scheduled pipeline results will appear here." />}
      </section>

      <section className="panel page-section">
        <div className="panel__header"><div><div className="eyebrow">Source health</div><h2>Latest adapter outcomes</h2><p>Public status only; internal error detail remains private.</p></div></div>
        {data.latest_source_runs.length ? (
          <div className="source-health-grid">
            {data.latest_source_runs.map((source, index) => (
              <article key={`${source.source_name}-${source.market_slug}-${index}`}>
                <div><strong>{source.source_name ?? "Unnamed source"}</strong><span>{source.market_slug ?? "Global"}</span></div>
                <StatusPill status={source.status} compact />
                <dl><div><dt>Discovered</dt><dd>{source.items_discovered}</dd></div><div><dt>Inserted</dt><dd>{source.items_inserted}</dd></div><div><dt>Retries</dt><dd>{source.retry_count}</dd></div></dl>
              </article>
            ))}
          </div>
        ) : <EmptyState title="No source-run details" description="Adapter health appears after a production pipeline run exports public status data." />}
      </section>

      {v15.data ? (
        <section className="panel page-section">
          <div className="panel__header"><div>
            <div className="eyebrow">PPI v1.5 (shadow)</div>
            <h2>Provider health, adapters & cutover readiness</h2>
            <p>The v1.5 quantitative pipeline runs alongside the headline series. Data providers, forecasting adapters, and the checklist that gates making Quant/Ensemble the public headline.</p>
          </div></div>

          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Provider</th><th>Kind</th><th>Status</th><th>Last success</th><th>Latency</th><th>Consecutive failures</th></tr></thead>
              <tbody>
                {v15.data.providers.length === 0 ? (
                  <tr><td colSpan={6}>No provider runs recorded yet.</td></tr>
                ) : v15.data.providers.map((p) => (
                  <tr key={p.name}>
                    <td><strong>{p.name}</strong>{p.recent_error ? <span>{p.recent_error}</span> : null}</td>
                    <td>{p.kind}</td>
                    <td><StatusPill status={p.status} compact /></td>
                    <td>{formatDateTime(p.last_success_at)}</td>
                    <td className="num">{p.last_latency_ms == null ? "—" : `${p.last_latency_ms} ms`}</td>
                    <td className="num">{p.consecutive_failures}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <dl className="definition-list definition-list--large" style={{ marginTop: "1rem" }}>
            {Object.entries(v15.data.adapters).map(([contract, status]) => (
              <div key={contract}><dt>{contract}</dt><dd><StatusPill status={status} compact /></dd></div>
            ))}
          </dl>

          <div className="v15-cutover">
            <strong>Headline series: {v15.data.cutover.current_headline_series}</strong>
            <p>{v15.data.cutover.note}</p>
            <ul>
              {v15.data.cutover.checklist.map((item, i) => <li key={i}>{item}</li>)}
            </ul>
            <p className="v15-footnote">
              {v15.data.cutover.quant_forecasts} quant forecasts · {v15.data.cutover.available_ensembles} available ensembles ·{" "}
              {v15.data.cutover.resolved_races} resolved races scored.
            </p>
          </div>
        </section>
      ) : null}
    </div>
  );
}
