export type NullableNumber = number | null;

export interface JobRun {
  run_key: string;
  job_name: string;
  trigger_type: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  // One of: canonical, noncanonical_mixed, contaminated, failed, adhoc. Only "canonical" (and
  // not superseded) runs are ever exported as overview.latest_run / system_status.latest_canonical_run.
  run_classification: string;
  superseded: boolean;
  markets_attempted: number;
  markets_succeeded: number;
  evidence_discovered: number;
  evidence_relevant: number;
  proposals_created: number;
  snapshots_written: number;
  error_count: number;
}

export interface MarketSummary {
  slug: string;
  tracking_id: string | null;
  question: string | null;
  category: string | null;
  region: string | null;
  status: string;
  active: boolean;
  closed: boolean;
  end_date: string | null;
  market_url: string | null;
  market_probability: NullableNumber;
  ppi_fair_value: NullableNumber;
  partisan_premium: NullableNumber;
  price_type: string | null;
  best_bid: NullableNumber;
  best_ask: NullableNumber;
  spread: NullableNumber;
  volume: NullableNumber;
  liquidity: NullableNumber;
  freshness_status: string;
  pipeline_status: string;
  is_stale: boolean;
  last_observed_at: string | null;
  last_fair_value_publication_at: string | null;
  revision_count: number;
  public_evidence_count: number;
}

export interface DailyIndexPoint {
  date: string | null;
  tracked_market_count: number;
  fresh_market_count: number;
  average_signed_premium: NullableNumber;
  average_absolute_premium: NullableNumber;
  liquidity_weighted_premium: NullableNumber;
  share_above_fair_value: NullableNumber;
  methodology_label: string | null;
  generated_at: string | null;
  status: string;
}

export interface RevisionSummary {
  market_slug: string;
  question: string | null;
  region: string | null;
  revision_number: number;
  fair_value: NullableNumber;
  previous_fair_value: NullableNumber;
  published_at: string | null;
  thesis: string | null;
}

export interface OverviewData {
  schema_version: string;
  generated_at: string | null;
  project: {
    name: string;
    abbreviation: string;
    formula: string;
    cadence: string;
  };
  coverage: {
    tracked_markets: number;
    fresh_markets: number;
    published_markets: number;
    resolved_predictions: number;
  };
  current_index: {
    average_signed_premium: NullableNumber;
    average_absolute_premium: NullableNumber;
    share_above_fair_value: NullableNumber;
    methodology_label: string;
  };
  latest_run: JobRun | null;
  largest_positive_premiums: MarketSummary[];
  largest_negative_premiums: MarketSummary[];
  largest_absolute_premiums: MarketSummary[];
  recent_fair_value_revisions: RevisionSummary[];
  index_history: DailyIndexPoint[];
}

export interface MarketsData {
  schema_version: string;
  generated_at: string | null;
  markets: MarketSummary[];
}

export interface Snapshot {
  observed_at: string | null;
  snapshot_date: string | null;
  snapshot_kind: string;
  market_probability: NullableNumber;
  price_type: string | null;
  best_bid: NullableNumber;
  best_ask: NullableNumber;
  last_trade: NullableNumber;
  spread: NullableNumber;
  volume: NullableNumber;
  liquidity: NullableNumber;
  ppi_fair_value: NullableNumber;
  partisan_premium: NullableNumber;
  freshness_status: string;
  pipeline_status: string;
  is_stale: boolean;
}

export interface FairValueComponent {
  type: string;
  probability: NullableNumber;
  weight: NullableNumber;
  source_label: string | null;
  source_url: string | null;
  observed_at: string | null;
  updated_at: string | null;
}

export interface EvidenceItem {
  title: string | null;
  url: string;
  source_type: string | null;
  source_name: string | null;
  published_at: string | null;
  discovered_at: string | null;
  category: string | null;
  direction: string | null;
  relevance_score: NullableNumber;
  source_quality: NullableNumber;
  estimated_magnitude: NullableNumber;
  summary: string | null;
}

export interface FairValueRevision {
  revision_number: number;
  fair_value: NullableNumber;
  previous_fair_value: NullableNumber;
  thesis: string | null;
  published_at: string | null;
  components: Record<
    string,
    {
      probability: NullableNumber;
      source_label: string | null;
      source_url: string | null;
      observed_at: string | null;
    }
  >;
  weights: Record<string, NullableNumber>;
  effective_weights: Record<string, NullableNumber>;
  evidence_urls: string[];
  is_correction: boolean;
}

export interface MarketSource {
  type: string;
  name: string | null;
  url: string | null;
}

export interface Prediction {
  market_slug: string;
  question: string | null;
  region: string | null;
  category: string | null;
  status: string;
  initial_publication_at: string | null;
  initial_ppi_fair_value: NullableNumber;
  initial_market_probability: NullableNumber;
  initial_thesis: string | null;
  resolved_outcome: NullableNumber;
  resolved_label: string | null;
  resolved_at: string | null;
  resolution_source_url: string | null;
  ppi_brier_score: NullableNumber;
  market_brier_score: NullableNumber;
  ppi_advantage: NullableNumber;
}

export interface MarketDetailData {
  schema_version: string;
  generated_at: string | null;
  market: MarketSummary & {
    description: string | null;
    rules: string | null;
    resolution_source: string | null;
    current_thesis: string | null;
    configured_weights: Record<string, NullableNumber>;
  };
  latest_snapshot: Snapshot | null;
  history: Snapshot[];
  components: FairValueComponent[];
  revisions: FairValueRevision[];
  evidence: EvidenceItem[];
  sources: MarketSource[];
  predictions: Prediction[];
  resolution: {
    outcome: NullableNumber;
    label: string | null;
    resolved_at: string | null;
    source_url: string | null;
  } | null;
}

export interface TrackRecordData {
  schema_version: string;
  generated_at: string | null;
  summary: {
    total_predictions: number;
    open_predictions: number;
    resolved_predictions: number;
    average_ppi_brier_score: NullableNumber;
    average_market_brier_score: NullableNumber;
    ppi_wins: number;
    market_wins: number;
    ties: number;
    sample_warning: string | null;
  };
  predictions: Prediction[];
}

export interface SourceRun {
  market_slug: string | null;
  source_name: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  items_discovered: number;
  items_inserted: number;
  retry_count: number;
}

export interface SystemStatusData {
  schema_version: string;
  generated_at: string | null;
  status: string;
  latest_run: JobRun | null;
  latest_canonical_run: JobRun | null;
  recent_runs: JobRun[];
  latest_source_runs: SourceRun[];
  summary: {
    tracked_markets: number;
    fresh_markets: number;
    stale_markets: number;
    latest_source_failures: number;
  };
}
