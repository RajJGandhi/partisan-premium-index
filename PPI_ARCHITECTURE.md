# PPI Architecture

## System shape

```text
Public/admin Streamlit application
            │
            ├── SQLAlchemy domain layer
            │       ├── SQLite (local)
            │       └── PostgreSQL (production)
            │
            ├── Daily pipeline / scheduler
            │       ├── Polymarket Gamma metadata
            │       ├── Polymarket CLOB order books/history
            │       ├── Evidence adapters
            │       ├── Relevance classifier abstraction
            │       ├── Proposal generator
            │       └── Canonical snapshots/index
            │
            └── Immutable publication/performance ledger
```

## Why the existing stack was preserved

The repository already used Python, SQLAlchemy, SQLite, Streamlit, Ollama and scheduled jobs. Replacing it with a Cloudflare-native TypeScript stack would have discarded working scoring, CLOB, LLM and paper-testing code. The production path therefore uses the native Python equivalents:

- Docker web service for Streamlit;
- Docker background worker for APScheduler;
- PostgreSQL for shared durable production state;
- SQLite for a single-process local installation.

## Main modules

### `app/ppi/polymarket.py`

- fetches a tracked Gamma market by ID or slug;
- fetches public CLOB books using token IDs;
- saves raw responses and hashes;
- applies the standardized price policy;
- supports official historical-price backfilling.

### `app/ppi/evidence.py`

Adapters:

- RSS/Atom;
- Google News RSS query;
- GDELT document discovery;
- configurable JSON/API feeds;
- manual observations;
- manually entered external-market observations.

Evidence is deduplicated per market using normalized title, canonical URL and content hash.

### `app/ppi/security.py`

- enforces HTTPS by default;
- rejects credentials embedded in URLs;
- rejects localhost, private, link-local, reserved and multicast addresses;
- supports optional domain allowlists;
- strips common tracking query parameters;
- verifies bcrypt passwords server-side.

### `app/ppi/classifier.py`

Provider interface:

- deterministic fallback;
- Ollama/Qwen;
- OpenAI-compatible chat-completion API.

All model responses validate against `EvidenceClassification`. Malformed or failed responses automatically fall back to deterministic classification and cannot crash the daily job.

### `app/ppi/methodology.py`

- validates probabilities and weights;
- calculates weighted fair values;
- preserves original and effective weights;
- handles missing-component redistribution explicitly;
- calculates partisan premium and Brier scores.

### `app/ppi/publication.py`

- approves/rejects proposals;
- creates immutable fair-value revisions;
- creates the initial prediction-ledger entry;
- records resolutions and performance.

### `app/ppi/pipeline.py`

One UTC daily run:

1. starts a `job_runs` record;
2. syncs enabled markets;
3. records source-level runs;
4. collects and classifies evidence;
5. proposes—but never silently publishes—fair-value changes;
6. upserts one canonical daily market snapshot;
7. generates the primary blind-LLM (Qwen) forecast for the market's twice-daily run slot (see `app/ppi/blind_forecast.py`), then joins it against the just-written price snapshot;
8. writes the daily aggregate index;
9. records sanitized failures;
10. writes a durable Markdown/JSON daily digest with market movements, evidence, proposals and failures;
11. optionally sends a compact Discord digest with a direct approval-queue link.

The pipeline commits after each market so a process interruption preserves completed work. A forced rerun updates the same daily snapshot instead of creating duplicates. The blind-forecast step runs inside a SQL SAVEPOINT per market so an unexpected forecast failure cannot abort snapshot/proposal work already committed for other markets in the same run.

### `app/ppi/blind_forecast.py`

The canonical, database-backed implementation of the primary Qwen-vs-Polymarket experiment described in `CLAUDE.md`. This is the convergence point between the two pipelines that previously existed in this repository:

- the DB-backed `app/ppi/pipeline.py`, which computed only a human-approved weighted blend and never called an LLM;
- the file/CSV-based `scripts/run_daily_experiment.py` + `scripts/run_llm_fair_values.py` ("Reality Spread"), which implemented a genuinely blind Ollama prompt but wrote to `data/llm_estimates/*.csv` outside the database and outside the scheduled pipeline.

`blind_forecast.py` reuses the same prompt contract (`fair_value_v0.1`, documented in `prompts/fair_value_prompt_v0_1.md`) so historical CSV runs and new database runs stay comparable, but persists to the `llm_forecasts` table instead of timestamped files:

- **Blindness**: `build_blind_evidence_packet` sources only `Market` identity fields and DB `EvidenceItem` rows; `assert_blind_packet` walks the packet at runtime and raises if any market-price-derived key (`comparison_price`, `yes_best_bid`, `spread`, `volume`, …) is present, so a future field addition cannot silently leak market consensus into the prompt.
- **Twice-daily append-only history**: `determine_run_slot` maps a generation timestamp onto `"<date>:primary"`, `"<date>:backup"`, or a timestamped `"<date>:adhoc-<HHMMSS>"` slot (based on `primary_run_hour_utc`/`backup_run_hour_utc`), and `llm_forecasts` has a unique constraint on `(market_id, run_slot)`. This replaces the previous one-row-per-day `market_snapshots` semantics, which could not represent two scheduled runs per day without one overwriting the other.
- **Immutability**: a slot already at status `OK` is returned unchanged on any later call — a valid primary-model probability is final for that run and is never edited. A slot that previously `FAILED` or was `SKIPPED_PROVIDER` may be retried in place, since it never produced a valid forecast to protect.
- **No silent fallback**: if `llm_provider` is not `ollama` or `openai_compatible` (e.g. the production GitHub Actions workflow, which runs on a hosted runner with no Ollama reachable and sets `LLM_PROVIDER=deterministic`), the row is recorded with status `SKIPPED_PROVIDER` and an explicit `error_message` — never a fabricated or deterministic-fallback value. If the model call or JSON-schema validation fails after a bounded number of retries, the row is recorded `FAILED` with `fair_value = NULL`.
- **Price joined after persistence**: `join_forecast_with_price` runs only after `generate_blind_forecast` has already committed the forecast row, computing `raw_ppi = polymarket_probability - llm_fair_value` from the same run's `market_snapshots` row. This preserves the "lock the forecast before price comparison" rule — the model can never see or be influenced by the price it will be compared against.

`scripts/run_daily_experiment.py` and `scripts/run_llm_fair_values.py` remain in the repository as the original file-based prototype and are still useful for offline experimentation against `data/tracked_markets_final.csv`, but the scheduled production path is now `app/ppi/pipeline.py` → `app/ppi/blind_forecast.py`. Decommissioning the file-based scripts is a later-phase cleanup once the database series has enough history to be trusted as the sole source of truth.

### `app/ppi/llm_forecast_view.py`, `app/ppi/llm_forecast_review.py`, and `app/ppi/public_forecast.py`

Read-side, review-side, and public-visibility support for the Streamlit "LLM Forecasts" page (public), the Administration → "LLM Forecasts" tab (admin-only), and the sanitized public export:

- `llm_forecast_view.py` is pure/read-only: freshness classification (`OK`/`STALE`/`ERROR`/`SKIPPED_PROVIDER`/`MISSING`) per market against `app_stale_hours`, the derived (non-native) confidence-interval heuristic used for the chart band, and the row/export shape shared by the on-screen table and the CSV download. It never writes to the database.
- `llm_forecast_review.py` lets an authenticated admin set `LLMForecast.reviewed_status` to `FLAGGED` (a genuine data-integrity concern, removing the forecast from public display) or back to `UNREVIEWED`, with a reviewer identity, timestamp, and notes. There is no `APPROVED_FOR_PUBLICATION` status — publication is automatic for a canonical forecast, never an admin action, per the research-integrity rules ("data-integrity review may only flag it, never approve, select, or edit it"). It is structurally incapable of touching `fair_value`, `confidence`, `should_abstain`, `rationale`, `key_uncertainties_json`, `base_rate_notes`, or `raw_response`; only the four `reviewed_*` columns are ever assigned.
- `public_forecast.py` (used by `scripts/export_public_bundle.py` and the Streamlit "System status" page) derives, per market, the current public forecast: the latest `LLMForecast` belonging to a canonical, non-superseded `JobRun`. `status == "OK"` publishes real values; `"ABSTAINED"` publishes an explicit abstention with no numeric value; anything else (`FAILED`/`SKIPPED_PROVIDER`) publishes `"ERROR"`; `reviewed_status == "FLAGGED"` overrides all of that to `"FLAGGED"` and suppresses display; no canonical forecast at all is `"NONE"`.

`LLMForecast` therefore carries two independent status concepts that must not be conflated: `status` (the generation outcome: `OK`/`ABSTAINED`/`FAILED`/`SKIPPED_PROVIDER`, set once by `generate_blind_forecast` and never edited) and `reviewed_status` (the data-integrity review outcome, editable by admins, defaulting to `UNREVIEWED` — a review flag, not a publication gate).

### `app/ppi/lock.py`

A filesystem PID-verified concurrency lock (`data/.ppi_pipeline.lock`), acquired for the duration of one `run_daily_pipeline` call. Defense in depth alongside the GitHub Actions `concurrency:` group on the scheduled workflow — that group prevents two *workflow* invocations from overlapping; this lock is the backstop for any out-of-band invocation (a manual local run, a stray second runner) that bypasses it. A stale lock (holder process no longer running) is reclaimed automatically; a lock held by a live process raises immediately rather than blocking, so a scheduled run that can't proceed fails fast and visibly (`status: "LOCKED"`, a benign non-failure outcome) instead of hanging.

### `app/ppi/run_classification.py`

Computes which of five base categories a `JobRun` belongs to — `canonical`, `noncanonical_mixed`, `contaminated`, `failed`, `adhoc` — from `pipeline_mode`, `status`, `trigger_type`, and whether any of the run's forecasts pulled in evidence that wasn't itself classified by a live model (`LLMForecast.evidence_all_live_classified`, set by `generate_blind_forecast`). Quality signals (mixed/contaminated) take priority over the scheduling signal (adhoc vs. canonical), so a manually-triggered strict run that turns out contaminated is reported as `contaminated`, never masked by `adhoc`. `mark_job_run_superseded` records (without deleting or editing anything) that a later run replaces an earlier one for reporting/publication purposes — used e.g. when a contaminated run is redone cleanly under a new `run_key`. Only a run that is `canonical` *and* not superseded (`is_canonical_and_current`) is ever exported as the public "latest run" — see `scripts/export_public_bundle.py`.

## Data model

Required production entities:

- `markets`
- `market_sources`
- `market_snapshots`
- `raw_market_responses`
- `evidence_items`
- `fair_value_components`
- `fair_value_proposals`
- `fair_value_revisions`
- `predictions`
- `market_resolutions`
- `daily_index`
- `job_runs`
- `source_runs`
- `admin_users`
- `llm_forecasts` — the primary blind Qwen series, append-only per `(market_id, run_slot)`, kept structurally separate from `fair_value_proposals`/`fair_value_revisions` so the human-approved weighted series and the primary model series can never be mixed.
- `blind_index_runs` — the aggregate blind-Qwen daily index (average/median/absolute signed premium, market count, model/prompt version), upserted per `run_key`, structurally separate from `daily_index` (the legacy human-weighted series' aggregate). See `app/ppi/blind_forecast.py`'s `compute_and_persist_blind_index`.

`job_runs` additionally carries `pipeline_mode` (`standard_mixed_fallback_allowed` / `strict_llm_only`), `run_classification` (`canonical` / `noncanonical_mixed` / `contaminated` / `failed` / `adhoc`, computed automatically), and `superseded_by_id` (an explicit, never-automatic pointer to a later run that replaces this one for reporting purposes) — see `app/ppi/run_classification.py`.

Legacy Reality Spread entities remain available for backward compatibility.

## PPI Quant v1.0 (shadow mode — `app/quant/`)

A deterministic quantitative election-forecasting engine that replaces the *shape* of the
forecast — `structured political data → quant model → margin distribution → Φ(μ/σ) → fair value` —
without (yet) replacing the headline series. Full methodology: `docs/research/PPI_QUANT_V1.md`.

- **Pure calculation modules**: `polling.py` (weighted margin, `n_eff`), `state_lean.py`,
  `national_environment.py`, `fundamentals.py` (Senate/Governor), `blend.py` (α, expected margin
  μ), `uncertainty.py` (σ_time/polling/office/status, RSS-combined), `probability.py`
  (`Φ` via `math.erf`), `data_quality.py` (STRONG/NORMAL/THIN/DEGRADED). Orchestrated by
  `engine.py::run_quant_forecast`, which **takes no market-price argument** — enforced by
  `tests/test_quant_market_independence.py` (signature check + static AST import check).
- **Config**: `config.py::MethodologyConfig` — one frozen, hashable object holding every constant;
  `version = "ppi-quant-v1.0"`, `config_hash()` persisted on every forecast. All v1.0 values are
  provisional (`PROVISIONAL_PARAMETERS`).
- **Adapters** (`adapters.py`): `statewide_race` SUPPORTED; `senate_control` EXPERIMENTAL (Monte
  Carlo, `senate_control.py`, ≥50k sims, deterministic per seed); `house_control` UNAVAILABLE (no
  fabricated number); everything else ABSTAIN.
- **Ensemble** (`ensemble.py`): `0.60·Quant + 0.20·GPT + 0.20·Claude`, predeclared, never re-fit;
  a missing component ⇒ `available = False`, no silent reweighting. Robustness HIGH/MEDIUM/LOW.
- **Evidence bundle** (`evidence_bundle.py`): immutable, timestamp-locked, market-free snapshot +
  content hash, stored per forecast.
- **New tables** (`app/db/models_quant.py`, additive): `races`, `race_candidates`,
  `poll_observations`, `national_environment_observations`, `historical_election_results`,
  `candidate_status_snapshots`, `data_provider_runs`, `quant_evidence_bundles`, `quant_forecasts`,
  `ensemble_forecasts`, `forecast_market_comparisons`, `forecast_resolutions`, `forecast_scores`,
  `methodology_versions`, `provider_health`. `llm_forecasts` gains `methodology_version`
  (default `ppi-v0-legacy-blind-llm`) + `forecast_role` (default `legacy_blind_llm`).
- **Append-only** (`app/quant/append_only.py`): `quant_forecasts` / `ensemble_forecasts` are
  append-only per `(race_id, run_key, methodology_version, revision)`; re-runs no-op, corrections
  add a linked new revision, `flag_integrity` never edits a number, `methodology_versions` is
  write-once.
- **Shadow runner** (`scripts/run_quant_shadow.py`, `make quant-shadow`): runs the engine against
  `data/seed/quant_example_races.json` and writes only the Quant tables — never `llm_forecasts`,
  `market_snapshots`, `daily_index`, `blind_index_runs`, or the public export.
- **`market_model_spread = MarketProbability − PPIFairValue`** is computed only in
  `forecast_market_comparisons`, after the forecast is persisted; it is an observation, not proof
  of partisan bias.

## PPI v1.5 pipeline + frontend (`app/pipeline_v15/`, `web/src/pages/PPIv15Page.tsx`)

The 10-stage twice-daily orchestrator (spec §41) + the public `/v15` surface. Shadow-only; the
headline series is unchanged. Full detail: `docs/research/PPI_V15_PIPELINE.md`,
`docs/research/PPI_CUTOVER.md`.

- **`orchestrator.py::run_v15_pipeline`** — discover → market snapshot → political data → validate
  → quant → evidence bundle → blind → ensemble → comparison → publish. One `JobRun`
  (`job_name="ppi-v15-daily"`); each race's stages 5-9 run in a SAVEPOINT so one failure doesn't
  roll back the rest; `as_of` is derived from the run_key so the whole pipeline is idempotent per
  slot.
- **`market_discovery.py`** — Polymarket Gamma discovery + `classify_market`; `SUPPORTED_STATEWIDE_RACE`
  above the confidence threshold → upsert `markets` + `races` + `market_yes_party`; the rest →
  append-only `market_classifications` QUARANTINED (never a fabricated forecast).
- **`comparison.py`** (stage 9) — reads the latest `MarketSnapshot`, orients via `market_yes_party`,
  writes `forecast_market_comparisons` with `market_model_spread = market − fair_value` per series
  and the ensemble robustness band. Runs strictly after the forecasts are persisted.
- **`persist.py`** — the shared `persist_quant_forecast` / evidence-bundle helper (also used by
  `run_quant_shadow.py`).
- **`cutover.py`** — `headline_series()` reads `PPI_HEADLINE_SERIES` (default `legacy_blind_llm`);
  `headline_forecast()` + `cutover_readiness()`. The flip is a one-line config change gated on the
  `PPI_CUTOVER.md` checklist; nothing here is automatic.
- **New table**: `market_classifications`. CLI `scripts/run_v15_daily.py`; workflow
  `.github/workflows/ppi-v15-daily.yml` (gated on `PPI_V15_ENABLED=true`); export
  `scripts/export_v15_bundle.py` → `web/public/data/v15/`.
- **Frontend**: `/v15` (spread-sorted races table, robustness badges) + `/v15/race/:id` (full
  breakdown — quant math, per-poll weights, blind rationales, σ components, Brier scores), a
  System-Status v1.5 section, and the 12-point methodology rewrite. v1.5 data is load-optional.

## Scoring / calibration / backtesting (`app/eval/`)

Resolved-outcome evaluation (spec sections 34-36, 47, 49). Full detail:
`docs/research/PPI_SCORING_BACKTEST_V1.md`.

- **`metrics.py`** — pure `brier` / `log_loss` (clamped) / `party_direction_error` /
  `calibration_bins` / `aggregate` (N + `low_confidence` always reported). `STANDARD_HORIZONS =
  (90, 60, 30, 14, 7, 1)`.
- **`series.py`** — `collect_series` reduces each series to a time-ordered `Observation` list, all
  oriented to `P(race.contract_yes_party wins)`; `market` / `legacy_llm` need `race.market_yes_party`
  and are dropped when it is unknown.
- **`scorer.py`** — `score_resolved_race` uses the **latest observation at or before**
  `election_date − h days` for each horizon (no lookahead) and upserts `forecast_scores`
  (idempotent — scores are a pure function of immutable forecasts + resolution).
- **`calibration.py`** — `build_calibration_report` groups by `series/horizon_days/office/state/
  methodology_version`; `comparisons` gives paired-by-(race,horizon) Brier deltas
  (`ensemble_vs_quant`, `quant_vs_market`, …) + `partisan_asymmetry`.
- **`leadlag.py`** — cross-correlation of two series' daily first-differences → `A_leads` /
  `B_leads` / `synchronous` / `insufficient_data`.
- **`backtest.py`** — `run_backtest(cycle, …)` behind a `PointInTimeGuard` that drops (or, in
  `--strict`, raises `PointInTimeError` on) any datum dated after the horizon cutoff, excludes
  cycle-C's own presidential result, and never feeds the resolution into the input. CLI
  `scripts/ppi_backtest.py --cycle 2026`.
- **Additive**: `races.contract_yes_party` (default `DEM`), `races.market_yes_party` (nullable).
  `make score` / `make backtest` / `make eval-test`.

## Blind benchmarks + v1.5 ensemble (`app/blind/`)

Independent GPT + Claude forecasts + the ensemble (spec sections 20-27). Full detail:
`docs/research/PPI_BLIND_BENCHMARKS_V1.md`.

- After the Quant forecast + its market-free `EvidenceBundle`, `run_blind_forecasts` sends the
  bundle (only) to `OpenAIBlindProvider` and `AnthropicBlindProvider` (`claude-opus-5` + adaptive
  thinking). **No market-price / Quant-probability / other-model parameter** — asserted by
  `tests/test_blind_market_independence.py`. One append-only `blind_benchmark_forecasts` row per
  provider; missing key/SDK → `SKIPPED_PROVIDER` (probability NULL, never fabricated).
- **Cost control**: a slot with an `OK` row at the same evidence hash + model + prompt version is
  reused (no re-call); a change appends a new revision.
- `compute_and_persist_ensemble` blends Quant + GPT + Claude with predeclared `0.60/0.20/0.20`
  (`app/quant/ensemble.py`). Any missing/abstained/failed component → `ensemble_forecasts.available
  = False`, present components never reweighted. Robustness band computed here (needs a market
  probability), strictly after persistence.
- `web_evidence.py` — bounded contamination-filtered web search (injected `search_fn`) → CLEAN
  items into `EvidenceBundle.current_news`; BLOCKED/QUARANTINED stored in `race_news_items` but
  excluded. Not an input to the deterministic Quant math (spec §20).
- **New tables**: `blind_benchmark_forecasts`, `race_news_items`. The legacy `llm_forecasts`
  series is untouched. `make quant-shadow-blind` / `--blind-stub`; live calls need
  `requirements-blind.txt` + `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.

## Provider / data-acquisition layer (`app/providers/`)

Automated political-data acquisition (spec sections 5-10, 21, 31, 42, 45). Full detail:
`docs/research/PPI_PROVIDERS_V1.md`.

- **`base.py`** — `BaseProvider.fetch` template method: disabled→EMPTY, fresh-cache check,
  bounded exponential-backoff retry, response validation, **last-known-good→STALE** on total
  failure (missing is never zero), `provider_health` update. `ProviderChain` runs providers in a
  fixed order and records one `data_provider_runs` row (`provider_requested` / `provider_used` /
  `fallback_reason`). `ProviderResult` carries full section-5 provenance + `content_hash`.
- **Chains** (`ingest.py::ingest_political_data`, scheduler stages 3-4):
  `election_history` (DDHQ results → seed CSV) → `historical_election_results`;
  `generic_ballot` (VoteHub → DDHQ → PollingSource → web) → `national_environment_observations`;
  `poll` (DDHQ ballot_test → PollingSource → web) → `poll_observations`;
  `candidate` (OpenFEC → seed → web) → `race_candidates` + `candidate_status_snapshots`.
  All observation writes dedup on a content hash; nothing is written as zero.
- **`normalize.py` / `race_identity.py`** — population/grade/sponsor normalization, canonical
  `nc-sen-2026` race ids, and deterministic→fuzzy→LLM-hook→abstain race/candidate matching.
- **`contamination.py`** — `PredictionMarketContaminationScanner`: BLOCKED (market/betting
  domain) / QUARANTINED (market-odds language) / CLEAN, for web evidence handed to blind LLMs.
- **`markets.py`** — Polymarket Gamma discovery + deterministic classification
  (SUPPORTED_STATEWIDE_RACE / SUPPORTED_SENATE_CONTROL / SUPPORTED_HOUSE_CONTROL / UNSUPPORTED /
  AMBIGUOUS); AMBIGUOUS is quarantined, never forecast.
- **`ingest.py::build_quant_input_from_db`** — assembles a market-free `QuantForecastInput` from
  the ingested tables: the `providers → DB → engine` bridge (`run_quant_shadow.py --from-db`).
- **New table**: `provider_cache` (append-only response cache + last-known-good). `make ingest`
  (live, graceful-degrade) / `make ingest-offline` (seed-file chains, no network/keys).
- No provider reads a prediction-market *price*; `app/quant` remains structurally market-blind.

## Reliability

- exponential retries on Polymarket, RSS, GDELT and JSON source calls;
- SSRF-safe redirect validation for administrator-configured source URLs;
- source and request timeouts;
- source-level failure isolation;
- incremental commits;
- primary and backup daily runs;
- evidence and snapshot uniqueness constraints;
- explicit `STALE`, `PARTIAL` and `FAILED` states;
- no raw stack traces in public status views.

## Production automation (self-hosted runner)

`.github/workflows/ppi-daily.yml` runs the scheduled canonical pipeline on a **self-hosted runner** (`runs-on: [self-hosted, macOS, ppi]`) registered on an always-on Mac with Ollama and `qwen3:8b` installed, with `LLM_PROVIDER=ollama` and `--strict-llm-only` set explicitly in the workflow. See `docs/SELF_HOSTED_RUNNER.md` for installation, secrets, keep-awake, reboot recovery, manual retry, and how to disable scheduling safely.

The workflow verifies Ollama is reachable, localhost-only (never `OLLAMA_HOST` bound beyond `127.0.0.1`), and has the model pulled *before* running the pipeline, and refuses to run at all rather than silently falling back — a missing self-hosted runner simply leaves the scheduled workflow queued (via the `ppi` runner label) instead of producing `SKIPPED_PROVIDER`/fallback-contaminated results.

A previous, now-superseded design ran on a GitHub-hosted `ubuntu-latest` runner with `LLM_PROVIDER=deterministic`, since a hosted runner cannot reach a local Ollama instance; that produced `llm_forecasts.status = SKIPPED_PROVIDER` for every market. `app/db/models.py`'s `JobRun.pipeline_mode`/`run_classification` still label any such historical run `noncanonical_mixed`/`adhoc` rather than deleting it — see `app/ppi/run_classification.py`.

An `LLM_PROVIDER=openai_compatible` hosted-model fallback remains available as an explicit, versioned, cost-capped option (a separately-labelled model series, never silently substituted for the primary Qwen series) if the self-hosted runner is ever unavailable for an extended period.

## Security boundary

The browser never receives LLM keys, database credentials, admin hashes or webhook URLs. All source fetching, classification, publication and administration occur server-side.
