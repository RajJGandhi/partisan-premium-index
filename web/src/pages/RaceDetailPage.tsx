import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { DataStamp } from "../components/DataStamp";
import { MetricCard } from "../components/MetricCard";
import { ErrorState, LoadingState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
import { usePublicData } from "../hooks/usePublicData";
import { formatProbability, formatShortDate } from "../lib/format";
import { v15Data } from "../lib/v15Data";

function pts(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)} pts`;
}

export function RaceDetailPage() {
  const { raceId = "" } = useParams();
  const { data, loading, error } = usePublicData(() => v15Data.race(raceId), [raceId]);

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading forecast breakdown…" /></div>;
  if (error) return <div className="shell-width page-space"><ErrorState error={error} /></div>;
  if (!data) {
    return (
      <div className="shell-width page-space">
        <Link className="back-link" to="/v15"><ArrowLeft size={15} /> Back to PPI v1.5</Link>
        <ErrorState error={new Error(`No v1.5 breakdown published for "${raceId}"`)} />
      </div>
    );
  }

  const q = data.quant;
  const u = q?.uncertainty;
  const f = data.fundamentals;
  const spread = data.market_model_spread;

  return (
    <div className="shell-width page-space">
      <Link className="back-link" to="/v15"><ArrowLeft size={15} /> Back to PPI v1.5</Link>
      <PageHeaderInline title={data.question} raceId={data.race_id} generatedAt={data.generated_at} />

      <section className="metrics-grid metrics-grid--four page-section--compact">
        <MetricCard label="Market" value={formatProbability(data.market_probability)} detail={data.quote_method ?? "no contract bound"} />
        <MetricCard label="PPI Ensemble"
          value={data.ensemble_available ? formatProbability(data.ensemble_probability) : "unavailable"}
          detail={data.ensemble_available ? "0.60 Q · 0.20 GPT · 0.20 Claude" : (data.ensemble_unavailable_reason ?? "a component is missing")}
          tone="accent" />
        <MetricCard label="Market − model spread" value={spread == null ? "—" : pts(spread * 100)}
          tone={spread == null ? "default" : spread > 0 ? "positive" : "negative"}
          detail={data.abs_spread == null ? "needs a priced contract" : "observation, not proof of bias"} />
        <MetricCard label="Robustness" value={data.robustness ?? "n/a"}
          detail={data.robustness === "HIGH" ? "models agree, diverge from market" : data.robustness === "LOW" ? "the gap is model disagreement" : "needs market + all models"} />
      </section>

      <section className="page-section detail-grid">
        <article className="panel detail-grid__wide">
          <div className="panel__header"><div><div className="eyebrow">Model breakdown</div><h2>Every independent estimate</h2></div></div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Series</th><th>Probability (D wins)</th><th>Notes</th></tr></thead>
              <tbody>
                <tr><td>PPI Quant</td><td className="num">{formatProbability(data.quant_probability)}</td>
                  <td>{q ? `data quality ${data.data_quality}` : "—"}</td></tr>
                {data.blind.filter((b) => b.provider === "openai").slice(-1).map((b) => (
                  <tr key="gpt"><td>GPT blind{b.is_stub ? " (stub)" : ""}</td>
                    <td className="num">{b.status === "OK" ? formatProbability(b.probability) : b.status}</td>
                    <td>{b.model}</td></tr>
                ))}
                {data.blind.filter((b) => b.provider === "anthropic").slice(-1).map((b) => (
                  <tr key="claude"><td>Claude blind{b.is_stub ? " (stub)" : ""}</td>
                    <td className="num">{b.status === "OK" ? formatProbability(b.probability) : b.status}</td>
                    <td>{b.model}</td></tr>
                ))}
                <tr><td>PPI Ensemble</td>
                  <td className="num">{data.ensemble_available ? formatProbability(data.ensemble_probability) : "unavailable"}</td>
                  <td>{data.ensemble_available ? "predeclared weights" : "not reweighted to present components"}</td></tr>
                <tr><td>Market</td><td className="num">{formatProbability(data.market_probability)}</td>
                  <td>{data.quote_method ?? "no contract bound"}</td></tr>
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel">
          <div className="panel__header"><div><div className="eyebrow">Quantitative forecast</div><h2>How Quant gets its number</h2></div></div>
          {q ? (
            <dl className="definition-list">
              <div><dt>Polling margin</dt><dd className="num">{pts(q.polling_margin)}</dd></div>
              <div><dt>Fundamentals margin</dt><dd className="num">{pts(q.fundamental_margin)}</dd></div>
              <div><dt>Polling weight (α)</dt><dd className="num">{q.poll_weight == null ? "—" : `${Math.round(q.poll_weight * 100)}%`}</dd></div>
              <div><dt>Expected margin (μ)</dt><dd className="num">{pts(q.expected_margin)}</dd></div>
              <div><dt>Forecast σ</dt><dd className="num">{u?.sigma_total ?? "—"}</dd></div>
              <div><dt>Win probability</dt><dd className="num"><strong>{formatProbability(q.p_dem_win)}</strong></dd></div>
              <div><dt>Effective polls</dt><dd className="num">{q.n_eff ?? "—"} of {q.used_poll_count ?? 0}</dd></div>
            </dl>
          ) : <p>Quant abstained: {(data.quant?.abstain_reasons ?? []).join("; ") || "insufficient data"}.</p>}
        </article>
      </section>

      <section className="page-section detail-grid">
        <article className="panel detail-grid__wide">
          <div className="panel__header"><div><div className="eyebrow">Polling inputs</div><h2>Each poll and its PPI weight</h2></div></div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Pollster</th><th>End</th><th>D margin</th><th>Weight</th><th>Recency</th><th>Sample</th><th>Quality</th><th>Sponsor</th></tr></thead>
              <tbody>
                {data.polling_inputs.length === 0 ? (
                  <tr><td colSpan={8}>No usable polls — the forecast is fundamentals-only.</td></tr>
                ) : data.polling_inputs.map((p, i) => (
                  <tr key={i}>
                    <td>{p.pollster}</td>
                    <td>{formatShortDate(p.end_date)}</td>
                    <td className="num">{pts(p.margin)}</td>
                    <td className="num"><strong>{p.weight?.toFixed(3) ?? "—"}</strong></td>
                    <td className="num">{p.weight_breakdown.recency ?? "—"}</td>
                    <td className="num">{p.weight_breakdown.sample ?? "—"}</td>
                    <td className="num">{p.weight_breakdown.quality ?? "—"}</td>
                    <td className="num">{p.weight_breakdown.sponsor ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="panel">
          <div className="panel__header"><div><div className="eyebrow">Fundamentals & uncertainty</div><h2>The other half</h2></div></div>
          <dl className="definition-list">
            <div><dt>State partisan lean</dt><dd className="num">{pts(f?.state_lean)}</dd></div>
            <div><dt>National environment</dt><dd className="num">{pts(f?.national_environment)}</dd></div>
            <div><dt>Incumbency</dt><dd className="num">{pts(f?.incumbency_adjustment)} ({f?.incumbent_party ?? "open"})</dd></div>
            <div><dt>σ time</dt><dd className="num">{u?.sigma_time ?? "—"}</dd></div>
            <div><dt>σ polling</dt><dd className="num">{u?.sigma_polling ?? "—"}</dd></div>
            <div><dt>σ office</dt><dd className="num">{u?.sigma_office ?? "—"}</dd></div>
            <div><dt>σ status</dt><dd className="num">{u?.sigma_status ?? "—"}</dd></div>
            <div><dt>σ total</dt><dd className="num"><strong>{u?.sigma_total ?? "—"}</strong></dd></div>
          </dl>
        </article>
      </section>

      <section className="page-section detail-grid">
        <article className="panel detail-grid__wide">
          <div className="panel__header"><div><div className="eyebrow">Independent models</div><h2>GPT & Claude rationale (blind to the market)</h2></div></div>
          {data.blind.filter((b) => b.status === "OK").length === 0 ? (
            <p>No blind benchmark forecasts yet — set OPENAI_API_KEY / ANTHROPIC_API_KEY, or run <code>--blind-stub</code>.</p>
          ) : (
            <div className="v15-rationale-grid">
              {["openai", "anthropic"].map((prov) => {
                const b = data.blind.filter((x) => x.provider === prov && x.status === "OK").slice(-1)[0];
                if (!b) return null;
                return (
                  <div key={prov} className="v15-rationale">
                    <div className="v15-rationale__head">
                      <strong>{prov === "openai" ? "GPT" : "Claude"}</strong>
                      <span className="num">{formatProbability(b.probability)}</span>
                      {b.is_stub ? <span className="v15-robust v15-robust--none">stub</span> : null}
                    </div>
                    <p>{b.rationale}</p>
                    {b.uncertainty_drivers.length ? (
                      <ul>{b.uncertainty_drivers.map((d, i) => <li key={i}>{d}</li>)}</ul>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </article>

        <article className="panel">
          <div className="panel__header"><div><div className="eyebrow">Data quality & provenance</div><h2>Status</h2></div></div>
          <dl className="definition-list">
            <div><dt>Data quality</dt><dd><StatusPill status={data.data_quality} compact /></dd></div>
            <div><dt>Methodology</dt><dd>{data.methodology_version ?? "—"}</dd></div>
            <div><dt>Evidence bundle</dt><dd className="num">{data.evidence_bundle?.content_hash.slice(0, 12) ?? "—"}</dd></div>
            <div><dt>Evidence cutoff</dt><dd>{formatShortDate(data.evidence_bundle?.forecast_timestamp)}</dd></div>
            <div><dt>Run key</dt><dd className="num">{data.latest_run_key ?? "—"}</dd></div>
            {data.resolved ? (
              <div><dt>Resolved</dt><dd>{data.resolved.dem_won >= 0.5 ? "Democrat won" : "Republican won"}
                {data.resolved.final_margin_dem != null ? ` (${pts(data.resolved.final_margin_dem)})` : ""}</dd></div>
            ) : null}
          </dl>
        </article>
      </section>

      {data.scores.length ? (
        <section className="page-section">
          <article className="panel">
            <div className="panel__header"><div><div className="eyebrow">Scored against the outcome</div><h2>Brier by series and horizon</h2></div></div>
            <div className="table-scroll">
              <table className="data-table">
                <thead><tr><th>Series</th><th>Horizon (days)</th><th>Forecast</th><th>Brier</th><th>Log loss</th></tr></thead>
                <tbody>
                  {data.scores.map((s, i) => (
                    <tr key={i}>
                      <td>{s.series}</td><td className="num">{s.horizon_days}</td>
                      <td className="num">{formatProbability(s.forecast_probability)}</td>
                      <td className="num">{s.brier_score?.toFixed(4) ?? "—"}</td>
                      <td className="num">{s.log_loss?.toFixed(4) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </article>
        </section>
      ) : null}
    </div>
  );
}

function PageHeaderInline({ title, raceId, generatedAt }: { title: string; raceId: string; generatedAt: string | null }) {
  return (
    <header className="page-header">
      <div>
        <div className="eyebrow">PPI v1.5 · {raceId}</div>
        <h1>{title}</h1>
        <p>Deterministic quant forecast, two blind LLM benchmarks, ensemble, and the market — with every input shown.</p>
      </div>
      <div className="page-header__actions"><DataStamp generatedAt={generatedAt} /></div>
    </header>
  );
}
