import { useState } from "react";
import { Link } from "react-router-dom";
import { DataStamp } from "../components/DataStamp";
import { PageHeader } from "../components/PageHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
import { usePublicData } from "../hooks/usePublicData";
import { formatProbability } from "../lib/format";
import { sortRaces, v15Data, type V15SortKey } from "../lib/v15Data";
import type { V15RaceSummary } from "../lib/v15Types";

const SORTS: Array<[V15SortKey, string]> = [
  ["abs_spread", "|Market − model|"],
  ["signed_spread", "Signed spread"],
  ["robustness", "Robustness"],
  ["dispersion", "Model dispersion"],
  ["data_quality", "Data quality"],
  ["ensemble", "Ensemble prob."],
];

function spread(value: number | null): string {
  if (value == null) return "—";
  const pts = value * 100;
  return `${pts > 0 ? "+" : ""}${pts.toFixed(1)} pts`;
}

function RobustnessBadge({ value }: { value: string | null }) {
  if (!value) return <span className="v15-robust v15-robust--none">n/a</span>;
  return <span className={`v15-robust v15-robust--${value.toLowerCase()}`}>{value}</span>;
}

function ModelCell({ label, value, status }: { label: string; value: number | null; status?: string }) {
  return (
    <span className="v15-model-cell">
      <small>{label}</small>
      <strong className="num">{value == null ? (status && status !== "OK" ? status : "—") : formatProbability(value)}</strong>
    </span>
  );
}

export function PPIv15Page() {
  const { data, loading, error } = usePublicData(v15Data.races, []);
  const [sortKey, setSortKey] = useState<V15SortKey>("abs_spread");

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading PPI v1.5 forecasts…" /></div>;
  if (error) return <div className="shell-width page-space"><ErrorState error={error} /></div>;
  if (!data || data.races.length === 0) {
    return (
      <div className="shell-width page-space">
        <PageHeader eyebrow="PPI v1.5" title="Quantitative forecasts + independent model benchmarks."
          description="A deterministic election model, two blind frontier-LLM benchmarks, and their ensemble — compared with the market." />
        <EmptyState title="No v1.5 forecasts published yet"
          description="The v1.5 pipeline runs in shadow mode. Once it has produced forecasts, they appear here." />
      </div>
    );
  }

  const rows: V15RaceSummary[] = sortRaces(data.races, sortKey);

  return (
    <div className="shell-width page-space">
      <PageHeader
        eyebrow="PPI v1.5"
        title="Quantitative forecast, blind LLM benchmarks, and the market."
        description="PPI Quant is a deterministic statistical model (polls + fundamentals → margin distribution → probability). GPT and Claude each forecast blind from the same evidence, with no market price. The ensemble is 0.60·Quant + 0.20·GPT + 0.20·Claude."
        actions={<DataStamp generatedAt={data.generated_at} />}
      />

      <section className={`status-banner status-banner--${data.headline_series === "legacy_blind_llm" ? "partial" : "ok"}`}>
        <div>
          <strong>Shadow series.</strong>
          <span>{data.headline_note}</span>
        </div>
      </section>

      <div className="v15-sortbar" role="group" aria-label="Sort races">
        <span>Sort by</span>
        {SORTS.map(([key, label]) => (
          <button key={key} type="button" className={sortKey === key ? "active" : ""} onClick={() => setSortKey(key)}>
            {label}
          </button>
        ))}
      </div>

      <div className="table-scroll">
        <table className="data-table v15-table">
          <thead>
            <tr>
              <th scope="col">Race</th>
              <th scope="col">Quant</th>
              <th scope="col">GPT</th>
              <th scope="col">Claude</th>
              <th scope="col">Ensemble</th>
              <th scope="col">Market</th>
              <th scope="col">Spread</th>
              <th scope="col">Robustness</th>
              <th scope="col">Data</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.race_id} className={r.robustness === "LOW" ? "v15-row--low" : undefined}>
                <td>
                  <Link to={`/v15/race/${r.race_id}`}>{r.race_id}</Link>
                  <span className="v15-race-sub">{r.state} {r.office} {r.cycle}
                    {r.resolved ? <em> · resolved {r.resolved.dem_won >= 0.5 ? "D" : "R"}</em> : null}</span>
                </td>
                <td><ModelCell label="quant" value={r.quant_probability} /></td>
                <td><ModelCell label="gpt" value={r.gpt_probability} status={r.gpt_status} /></td>
                <td><ModelCell label="claude" value={r.claude_probability} status={r.claude_status} /></td>
                <td>
                  {r.ensemble_available
                    ? <ModelCell label="ensemble" value={r.ensemble_probability} />
                    : <span className="v15-model-cell"><small>ensemble</small><strong>unavailable</strong></span>}
                </td>
                <td className="num">{r.market_probability == null ? "—" : formatProbability(r.market_probability)}</td>
                <td className={`num v15-spread${(r.market_model_spread ?? 0) > 0 ? " v15-spread--pos" : (r.market_model_spread ?? 0) < 0 ? " v15-spread--neg" : ""}`}>
                  {spread(r.market_model_spread)}
                </td>
                <td><RobustnessBadge value={r.robustness} /></td>
                <td><StatusPill status={r.data_quality} compact /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="v15-footnote">
        A large market–model gap only counts as signal when PPI's own models agree (HIGH robustness). A gap driven by
        Quant/GPT/Claude disagreeing (LOW) is model uncertainty, not a market anomaly. Market and spread are blank until a
        Polymarket contract is bound and priced. This is an observation, not proof of partisan bias.
      </p>
    </div>
  );
}
