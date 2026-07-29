import {
  Archive,
  BarChart3,
  BookCheck,
  BrainCircuit,
  Database,
  Eye,
  FileSearch,
  GitBranch,
  Scale,
  ShieldCheck,
} from "lucide-react";
import { PageHeader } from "../components/PageHeader";

const components = [
  ["Polling", "35%", "Qualifying polls and polling averages, adjusted for recency, methodology and sample quality."],
  ["Forecasts & fundamentals", "25%", "Structural models, candidate quality, incumbency, economic context and electoral fundamentals."],
  ["Comparable markets", "20%", "Related races, chamber-control markets and logically connected contracts used as cross-checks."],
  ["Expert consensus", "10%", "Public assessments from credible forecasters, analysts and subject-matter experts."],
  ["Campaign & news", "10%", "A bounded adjustment for material developments not yet incorporated into slower-moving inputs."],
];

const safeguards = [
  [ShieldCheck, "Human approval is mandatory", "Automated systems may collect, classify and propose. They cannot publish a fair value."],
  [Archive, "History is append-only", "New revisions preserve earlier values, timestamps and theses rather than overwriting them."],
  [Eye, "Inputs are visible", "The public can inspect component values, weights, accepted evidence and the current thesis."],
  [BrainCircuit, "LLM use is bounded", "Models may classify relevance and summarize evidence. They do not produce unreviewed public conclusions."],
];

export function MethodologyPage() {
  return (
    <div className="shell-width page-space methodology-page">
      <PageHeader
        eyebrow="Methodology"
        title="A transparent fair-value process, not a black box."
        description="PPI is designed to make disagreement inspectable: what the market says, what the independent estimate says, why they differ, and how both performed."
      />

      <section className="methodology-hero">
        <div>
          <span className="eyebrow">Core measure</span>
          <h2>Partisan premium</h2>
          <div className="formula-card">
            <span>Market implied probability</span>
            <strong>−</strong>
            <span>PPI fair value</span>
            <strong>=</strong>
            <span>Partisan premium</span>
          </div>
          <p>A positive result means the market is above PPI's independent estimate. A negative result means it is below it. The label describes the gap; it does not prove the cause.</p>
        </div>
        <aside>
          <Scale size={26} />
          <strong>What PPI is testing</strong>
          <p>Whether political prediction markets exhibit persistent, directionally meaningful gaps from a disciplined independent fair-value process—and whether those gaps predict eventual accuracy.</p>
        </aside>
      </section>

      <section className="page-section methodology-section" id="components">
        <div className="section-heading">
          <div className="eyebrow">Fair-value construction</div>
          <h2>Five components, each with public provenance.</h2>
          <p>The default weights create discipline while still allowing market-specific configuration when the evidence environment differs.</p>
        </div>
        <div className="methodology-components">
          {components.map(([name, weight, description], index) => (
            <article key={name}>
              <div className="methodology-components__number">0{index + 1}</div>
              <div><h3>{name}</h3><p>{description}</p></div>
              <strong>{weight}</strong>
            </article>
          ))}
        </div>
      </section>

      <section className="page-section pipeline-section" id="pipeline">
        <div className="section-heading">
          <div className="eyebrow">Pipeline</div>
          <h2>From raw market data to a public revision.</h2>
        </div>
        <div className="pipeline-flow">
          {[
            [Database, "Synchronize", "Market metadata, order books, prices, spreads, volume and liquidity."],
            [FileSearch, "Collect evidence", "Approved feeds, official sources, APIs and manually added observations."],
            [BrainCircuit, "Classify", "Relevance, source quality, direction, magnitude and review requirements."],
            [BarChart3, "Construct proposal", "Latest eligible components are combined using configured effective weights."],
            [BookCheck, "Human review", "An administrator verifies inputs, edits the thesis and approves or rejects."],
            [GitBranch, "Publish revision", "The value, evidence and timestamp are appended to immutable public history."],
          ].map(([Icon, title, copy], index) => {
            const TypedIcon = Icon as typeof Database;
            return (
              <article key={title as string}>
                <span className="pipeline-flow__number">{String(index + 1).padStart(2, "0")}</span>
                <span className="pipeline-flow__icon"><TypedIcon size={20} /></span>
                <h3>{title as string}</h3>
                <p>{copy as string}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="page-section methodology-section" id="safeguards">
        <div className="section-heading">
          <div className="eyebrow">Editorial safeguards</div>
          <h2>Automation stops before judgment becomes public.</h2>
        </div>
        <div className="safeguard-grid">
          {safeguards.map(([Icon, title, copy]) => {
            const TypedIcon = Icon as typeof ShieldCheck;
            return (
              <article key={title as string}>
                <TypedIcon size={23} />
                <h3>{title as string}</h3>
                <p>{copy as string}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="page-section two-column-section two-column-section--balanced">
        <article className="panel prose-block">
          <div className="eyebrow">Observation cadence</div>
          <h2>Twice daily, plus material events</h2>
          <p>The full pipeline runs twice per day. Additional manual runs may follow debates, major polls, candidate withdrawals, court decisions, election results or other events that could materially change the estimate.</p>
          <p>This cadence is designed for a research index, not high-frequency trading. PPI values change when the evidence changes—not merely because the market price moves.</p>
        </article>
        <article className="panel prose-block">
          <div className="eyebrow">Resolution and scoring</div>
          <h2>Predictions remain falsifiable</h2>
          <p>When a market resolves, its initial public PPI probability and the market probability at publication are scored against the binary outcome using Brier scores.</p>
          <p>Lower scores are better. Aggregate comparisons are displayed with explicit sample-size warnings until enough markets have resolved.</p>
        </article>
      </section>

      <section className="page-section limitations-panel">
        <div><div className="eyebrow">Limitations</div><h2>What the index cannot prove</h2></div>
        <div className="limitations-panel__grid">
          <p><strong>Selection effects.</strong> Tracked markets are curated and may not represent every political prediction market.</p>
          <p><strong>Model risk.</strong> PPI fair values depend on component quality, weighting choices and human judgment.</p>
          <p><strong>Causal claims.</strong> A premium can be consistent with partisan enthusiasm without proving that partisanship caused it.</p>
          <p><strong>Small samples.</strong> Early wins or losses should not be treated as strong evidence until many markets resolve.</p>
        </div>
      </section>
    </div>
  );
}
