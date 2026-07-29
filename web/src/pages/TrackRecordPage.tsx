import { Award, Gauge, Scale, Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DataStamp } from "../components/DataStamp";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, EmptyState } from "../components/StateViews";
import { StatusPill } from "../components/StatusPill";
import { usePublicData } from "../hooks/usePublicData";
import { publicData } from "../lib/data";
import { formatDateTime, formatProbability } from "../lib/format";

export function TrackRecordPage() {
  const { data, loading, error } = usePublicData(publicData.trackRecord, []);
  const [status, setStatus] = useState("all");

  const predictions = useMemo(
    () => (data?.predictions ?? []).filter((prediction) => status === "all" || prediction.status.toLowerCase() === status),
    [data, status],
  );

  if (loading) return <div className="shell-width page-space"><LoadingState label="Loading track record…" /></div>;
  if (error || !data) return <div className="shell-width page-space"><ErrorState error={error ?? new Error("Track record unavailable")} /></div>;

  return (
    <div className="shell-width page-space">
      <PageHeader
        eyebrow="Track record"
        title="Performance is judged after the outcome, not before it."
        description="Every published prediction keeps its initial PPI value, market probability, thesis, resolution and Brier score."
        actions={<DataStamp generatedAt={data.generated_at} />}
      />

      {data.summary.sample_warning ? (
        <div className="notice notice--neutral"><Scale size={19} /><div><strong>Early sample warning</strong><span>{data.summary.sample_warning} Treat early comparisons as descriptive, not conclusive.</span></div></div>
      ) : null}

      <section className="metrics-grid metrics-grid--four page-section--compact">
        <MetricCard label="Public predictions" value={data.summary.total_predictions} detail={`${data.summary.open_predictions} still open`} icon={Gauge} />
        <MetricCard label="Resolved" value={data.summary.resolved_predictions} detail="Outcomes with final scores" icon={Award} />
        <MetricCard label="Average PPI Brier" value={data.summary.average_ppi_brier_score?.toFixed(3) ?? "—"} detail="Lower is better" icon={Trophy} tone="accent" />
        <MetricCard label="Head-to-head" value={`${data.summary.ppi_wins}–${data.summary.market_wins}`} detail={`${data.summary.ties} ties`} icon={Scale} />
      </section>

      <section className="panel page-section">
        <div className="panel__header panel__header--responsive">
          <div><div className="eyebrow">Prediction ledger</div><h2>Initial values and eventual outcomes</h2><p>The initial forecast is preserved even when PPI later publishes revisions.</p></div>
          <label className="inline-select"><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All predictions</option><option value="open">Open</option><option value="resolved">Resolved</option></select></label>
        </div>

        {predictions.length ? (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Market</th><th>Status</th><th>Published</th><th>PPI</th><th>Market</th><th>Outcome</th><th>PPI Brier</th><th>Market Brier</th><th>Advantage</th></tr></thead>
              <tbody>
                {predictions.map((prediction) => (
                  <tr key={`${prediction.market_slug}-${prediction.initial_publication_at}`}>
                    <td><Link to={`/markets/${prediction.market_slug}`}><strong>{prediction.question}</strong><span>{prediction.region} · {prediction.category}</span></Link></td>
                    <td><StatusPill status={prediction.status} compact /></td>
                    <td>{formatDateTime(prediction.initial_publication_at)}</td>
                    <td>{formatProbability(prediction.initial_ppi_fair_value)}</td>
                    <td>{formatProbability(prediction.initial_market_probability)}</td>
                    <td>{prediction.resolved_label ?? formatProbability(prediction.resolved_outcome)}</td>
                    <td>{prediction.ppi_brier_score?.toFixed(3) ?? "—"}</td>
                    <td>{prediction.market_brier_score?.toFixed(3) ?? "—"}</td>
                    <td className={prediction.ppi_advantage != null && prediction.ppi_advantage > 0 ? "score-positive" : prediction.ppi_advantage != null && prediction.ppi_advantage < 0 ? "score-negative" : ""}>{prediction.ppi_advantage?.toFixed(3) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No predictions in this view" description="Published fair values appear here once their public prediction records are established." />}
      </section>

      <section className="page-section brier-explainer">
        <div><div className="eyebrow">Scoring</div><h2>Why Brier scores?</h2></div>
        <div className="brier-explainer__formula">(forecast − outcome)<sup>2</sup></div>
        <p>A probability of 70% that resolves YES scores 0.09. A probability of 70% that resolves NO scores 0.49. Lower aggregate scores indicate better calibration and accuracy.</p>
      </section>
    </div>
  );
}
