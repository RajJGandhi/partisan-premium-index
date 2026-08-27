// Types for the PPI v1.5 public data bundle (web/public/data/v15/*, produced by
// scripts/export_v15_bundle.py). See docs/research/PPI_QUANT_V1.md.

export interface V15RaceSummary {
  race_id: string;
  state: string;
  office: string;
  cycle: number;
  question: string;
  contract_yes_party: string;
  data_quality: string | null;
  abstained: boolean | null;
  quant_probability: number | null;
  gpt_probability: number | null;
  claude_probability: number | null;
  gpt_status: string;
  claude_status: string;
  ensemble_probability: number | null;
  ensemble_available: boolean;
  ensemble_unavailable_reason: string | null;
  market_probability: number | null;
  market_model_spread: number | null;
  abs_spread: number | null;
  quote_method: string | null;
  robustness: string | null;
  dispersion: number | null;
  liquidity: number | null;
  latest_run_key: string | null;
  generated_at: string | null;
  methodology_version: string | null;
  resolved: { dem_won: number; final_margin_dem: number | null; resolved_at: string | null } | null;
}

export interface V15RacesData {
  schema_version: string;
  generated_at: string;
  headline_series: string;
  headline_note: string;
  races: V15RaceSummary[];
}

export interface V15PollInput {
  pollster: string | null;
  end_date: string | null;
  margin: number | null;
  weight: number | null;
  weight_breakdown: Record<string, number | null>;
}

export interface V15BlindForecast {
  provider: string;
  model: string;
  status: string;
  probability: number | null;
  should_abstain: boolean | null;
  rationale: string | null;
  uncertainty_drivers: string[];
  is_stub: boolean;
}

export interface V15Score {
  series: string;
  horizon_days: number;
  brier_score: number | null;
  log_loss: number | null;
  forecast_probability: number | null;
  outcome: number | null;
}

export interface V15RaceDetail extends V15RaceSummary {
  schema_version: string;
  headline_series: string;
  quant: {
    polling_margin: number | null;
    fundamental_margin: number | null;
    poll_weight: number | null;
    expected_margin: number | null;
    p_dem_win: number | null;
    p_dem_win_uncapped: number | null;
    n_eff: number | null;
    used_poll_count: number | null;
    latest_poll_date: string | null;
    uncertainty: {
      sigma_total: number | null;
      sigma_time: number | null;
      sigma_polling: number | null;
      sigma_office: number | null;
      sigma_status: number | null;
    } | null;
    abstain_reasons: string[];
    config_hash: string;
  } | null;
  fundamentals: {
    state_lean: number | null;
    national_environment: number | null;
    incumbency_adjustment: number | null;
    incumbent_party: string | null;
    fundamental_margin: number | null;
  } | null;
  polling_inputs: V15PollInput[];
  blind: V15BlindForecast[];
  evidence_bundle: {
    content_hash: string;
    forecast_timestamp: string | null;
    news: Array<{
      title: string;
      url: string | null;
      source_domain: string | null;
      category: string | null;
      published_at: string | null;
    }>;
  } | null;
  history: Array<{ run_key: string; generated_at: string | null; quant: number | null; ensemble: number | null }>;
  scores: V15Score[];
}

export interface V15ProviderStatus {
  schema_version: string;
  generated_at: string;
  providers: Array<{
    name: string;
    kind: string;
    status: string;
    is_stale: boolean;
    consecutive_failures: number;
    last_success_at: string | null;
    last_attempt_at: string | null;
    last_latency_ms: number | null;
    latest_data_timestamp: string | null;
    recent_error: string | null;
  }>;
  adapters: Record<string, string>;
  cutover: {
    current_headline_series: string;
    checklist: string[];
    quant_forecasts: number;
    available_ensembles: number;
    resolved_races: number;
    note: string;
  };
  latest_job_run?: {
    run_key: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    markets_attempted: number;
    markets_succeeded: number;
  };
}

export interface V15CalibrationGroup {
  group: Record<string, unknown>;
  n: number;
  mean_brier: number | null;
  mean_log_loss: number | null;
  mean_predicted: number | null;
  resolution_rate: number | null;
  direction_error_rate: number | null;
  calibration_error: number | null;
  low_confidence: boolean;
}

export interface V15CalibrationData {
  schema_version: string;
  generated_at: string;
  group_by: string[];
  n_score_rows: number;
  n_resolved_races: number;
  overall: V15CalibrationGroup;
  groups: V15CalibrationGroup[];
  comparisons: Record<string, unknown>;
}
