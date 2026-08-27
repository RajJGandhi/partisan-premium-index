# PPI v1.5 pipeline + frontend (Phases E + H)

**Status:** implemented in `app/pipeline_v15/` + `web/src/pages/PPIv15Page.tsx` /
`RaceDetailPage.tsx`. Shadow-only — the public headline is unchanged (see
`docs/research/PPI_CUTOVER.md`).

## The 10-stage orchestrator (`app/pipeline_v15/orchestrator.py`, spec §41)

`run_v15_pipeline(session, *, race_configs, run_key, blind_mode, discovery_provider,
market_client, ingest_kwargs, ...)` runs, recording one `JobRun` (`job_name = "ppi-v15-daily"`):

| # | Stage | Implementation |
|---|---|---|
| 1 | discover | `market_discovery.discover_and_bind` — Polymarket Gamma discovery + `classify_market`; `SUPPORTED_STATEWIDE_RACE` above `MARKET_CLASSIFY_MIN_CONFIDENCE` → upsert `markets` + `races` + `market_yes_party`; everything else → `market_classifications` QUARANTINED. Skipped without a `discovery_provider`. |
| 2 | market snapshot | duck-typed `market_client.snapshot_market(session, market)` for linked contracts. Skipped without a client (build-to-seam). |
| 3 | political data | `providers.ingest.ingest_political_data` — poll / generic-ballot / election-history / candidate chains → the append-only observation tables. |
| 4 | validate | counts invalid `poll_observations` + the ingest `poll_skipped` reasons. |
| 5 | quant forecast | per race, inside a **SAVEPOINT**: `build_quant_input_from_db` → `StatewideRaceAdapter` → `persist_quant_forecast` (`app/pipeline_v15/persist.py`, shared with the shadow runner). |
| 6 | evidence bundle | `build_quant_evidence_bundle` → write-once `quant_evidence_bundles`. |
| 7 | blind forecasts | `run_blind_forecasts` (`blind_mode` = `stub` / `live` / off). |
| 8 | ensemble | `compute_and_persist_ensemble` — `available = False` when a component is missing, never reweighted. |
| 9 | comparison | `comparison.join_forecasts_with_market` — `market_model_spread = market − fair_value` per series into `forecast_market_comparisons`, oriented via `market_yes_party`, robustness on the ensemble row. |
| 10 | publish | v1.5 rows are append-only + immutable; the headline is **not** flipped here. |

`as_of` is derived deterministically from the run_key (`ppi-v15:<date>:<slot>` → 13:00 / 01:00
UTC), so **every stage of a slot shares one evidence cutoff and the whole pipeline is idempotent
per run_key**. One race raising inside its SAVEPOINT is recorded `status = "ERROR"` and does not
roll back the others (`job.status` becomes `PARTIAL`).

CLI: `scripts/run_v15_daily.py` (`--offline` seed chains, `--blind` / `--blind-stub`, `--discover`).
Workflow: `.github/workflows/ppi-v15-daily.yml` — gated on repo var `PPI_V15_ENABLED=true`,
twice-daily 09:20 / 21:20 America/Toronto, runs alongside `ppi-daily.yml`, exports the v1.5 bundle,
scores newly resolved races.

## Schema

Additive: `market_classifications` (append-only discovery/classification log). Two columns on
`races` (Phase I): `contract_yes_party` (default `DEM`), `market_yes_party` (nullable → market /
legacy series excluded from that race's scoring). `python scripts/migrate_db.py` is idempotent.

## Public export (`scripts/export_v15_bundle.py`, spec §37-40)

Writes `web/public/data/v15/` — **additive**, never touches `export_public_bundle.py`:

- `races.json` — one summary row per race (Quant / GPT / Claude / Ensemble / Market probabilities,
  `market_model_spread`, robustness, dispersion, data quality, resolved outcome).
- `race/<race_id>.json` — full breakdown: the quant math (`polling_margin`, `fundamental_margin`,
  `poll_weight`, `expected_margin`, σ components, `p_dem_win` capped + uncapped), **every poll and
  its PPI weight with the recency/sample/quality/sponsor breakdown**, fundamentals, blind
  rationales + uncertainty drivers, evidence-bundle hash + contamination-filtered news, history,
  and per-series Brier at each resolved horizon.
- `provider-status.json` — `provider_health` rows, adapter capabilities, `cutover_readiness`, last
  `ppi-v15-daily` job run.
- `calibration.json` — the calibration report.

`assert_safe` refuses any secret-looking key or `sk-`-prefixed string before writing.

## Frontend (Phase H, `web/`)

- **`/v15`** (`PPIv15Page`) — races table sorted by `|market − model|` (also signed spread,
  robustness, model dispersion, data quality, ensemble prob). Robustness badges: HIGH stands out,
  LOW rows are de-emphasised. A shadow-series banner reads from `headline_series`.
- **`/v15/race/:raceId`** (`RaceDetailPage`) — headline cards (Market / Ensemble / spread /
  robustness), model-breakdown table, the quantitative-forecast block, the polling-inputs table
  with per-poll weights, fundamentals + every σ component, GPT/Claude rationale, data-quality &
  provenance, and the Brier-by-series-and-horizon table when resolved.
- **System Status** gains a "PPI v1.5 (shadow)" section: provider-health rows, adapter
  capabilities, and the cutover checklist.
- **Methodology** gains the 12-point v1.5 explanation (spec §39).
- v1.5 data is **load-optional**: a missing file renders a friendly empty state, never an error.

`npm run check` (tsc + data check) and `npm run build` pass.

## Deferred

- Live Polymarket discovery + market-snapshot wiring (stages 1-2 are opt-in seams; the classifier
  and the `snapshot_market` duck-type are done).
- The homepage (`/`) itself still shows the legacy series; the v1.5 spread-sorted view lives at
  `/v15` until the cutover.
- Making Quant/Ensemble the headline — gated on `docs/research/PPI_CUTOVER.md`.
- Real end-to-end run against live APIs (needs keys; the whole pipeline runs offline today).
