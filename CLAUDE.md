# Partisan Premium Index (PPI)

## Purpose and source of truth

- PPI is a forward-tested research product comparing a **blind LLM fair probability** with the current Polymarket probability.
- Canonical formula: `raw_ppi = polymarket_probability - llm_fair_value`.
- Positive raw PPI means Polymarket is more bullish than the model; negative raw PPI means Polymarket is less bullish.
- The primary experiment is **DeepSeek V4 Flash 0731 (via OpenRouter) versus Polymarket/retail**, not a human-weighted forecasting product and not a trading bot. Qwen3 8B (local Ollama) was the primary model through 2026-08-25 and is now the secondary comparison series -- see `docs/research/DEEPSEEK_PRIMARY_CUTOVER_DEVIATION_20260826.md`.
- This file overrides conflicting product descriptions in `README.md`, `PRD.md`, `PPI_ARCHITECTURE.md`, and `PPI_METHODOLOGY.md` until those documents are reconciled.
- Never place trades, request wallet keys, or add real-money execution.

## Current milestone

Treat the project as incomplete until all of these work together:

1. The 12-market production universe loads correctly.
2. Gamma metadata and CLOB prices are fetched reliably.
3. A blinded DeepSeek estimate (plus the Qwen comparison estimate) runs for every eligible market on every scheduled run.
4. Raw API payloads, evidence, prompts, model outputs, and snapshots are durably stored.
5. Re-running the same logical job is idempotent.
6. New scheduled runs append immutable history twice per day.
7. The Streamlit admin/research app displays real history and can review/publish model outputs.
8. The React public site displays current and historical PPI data from sanitized exports.
9. Raw and standardized PPI, aggregate daily statistics, calibration, Brier scores, and track record are correct.
10. Automation, monitoring, tests, and documentation are production-ready.

Prioritize this end-to-end path over unrelated features or cosmetic expansion.

## Canonical architecture

This is a monorepo with two product surfaces and one research engine:

- `app/`: Python research engine, database layer, ingestion, scoring, local-LLM integration, and Streamlit admin/research UI.
- `scripts/`: CLI entry points, migrations, seeding, experiments, exports, and operations.
- `tests/`: deterministic Python test suite.
- `web/`: React/Vite public site deployed to Cloudflare Pages.
- `data/seed/`: committed market definitions and source-pack configuration.
- `data/`: local/generated research artifacts; do not commit runtime databases or bulk generated outputs unless they are intentional fixtures.
- `prompts/`: versioned forecasting prompts.
- `docs/`: architecture, methodology, operations, deployment, and troubleshooting.

The repository currently contains two partially separate systems:

- `app/ppi/` implements a human-approved weighted-fair-value workflow.
- `scripts/run_daily_experiment.py` and `scripts/run_llm_fair_values.py` implement the intended blinded LLM experiment.

Converge these into **one canonical database-backed pipeline**. Reuse proven code, preserve historical artifacts, and remove duplicate sources of truth only after migration and verification. Do not keep two competing definitions of PPI.

### PPI Quant v1.0 — `app/quant/` (shadow mode; not yet canonical)

A deterministic quantitative election-forecasting engine (`structured political data → quant model → margin distribution → Φ(μ/σ) → fair value`) added as the first phase of the PPI v1.5 rewrite. It runs in **shadow mode only**: `scripts/run_quant_shadow.py` (`make quant-shadow`) writes the new append-only `quant_forecasts` / `ensemble_forecasts` tables and never touches `llm_forecasts`, `market_snapshots`, `daily_index`, `blind_index_runs`, or the public export. The legacy blind-LLM series (`raw_ppi = market − llm_fair_value`) remains the headline. `run_quant_forecast` takes **no market-price argument** (enforced by tests). Methodology + deferred scope: `docs/research/PPI_QUANT_V1.md`. Existing `llm_forecasts` rows are retained and labelled `methodology_version = ppi-v0-legacy-blind-llm`, `forecast_role = legacy_blind_llm`.

### PPI provider / data-acquisition layer — `app/providers/`

Automated political-data acquisition that feeds the Quant engine (spec sections 5-10, 21, 31, 42, 45). `BaseProvider` + `ProviderChain` give caching, retry/backoff, last-known-good (`provider_cache`), `provider_health`, and `data_provider_runs` audit rows; missing data is `STALE`/`EMPTY`, never zero. Chains: `election_history`, `generic_ballot`, `poll`, `candidate` — `ingest_political_data` (`make ingest` / `make ingest-offline`) writes the de-duplicated observations, `build_quant_input_from_db` + `scripts/run_quant_shadow.py --from-db` run providers → DB → engine. `contamination.py` blocks/quarantines prediction-market sources in web evidence; `markets.py` classifies Polymarket contracts (AMBIGUOUS → quarantine, never a fabricated forecast). No provider reads a market *price*. Detail: `docs/research/PPI_PROVIDERS_V1.md`. Only manual config is keys/base URLs in `.env` (Decision Desk HQ = the spec's "Decision Labs"). Live endpoints (verified 2026-08): `votehub_race_polls` + `votehub_generic_ballot` are the primary poll / generic-ballot sources (`api.votehub.com`, public, no key); DDHQ polling (`polling.decisiondeskhq.com`, public) is the fallback; `decisiondesk_election_history` reads DDHQ Results API v4 `/api/v4/race-calls` (OAuth2 client-credentials) for state lean and is disabled → seed-CSV fallback until `DECISIONDESK_CLIENT_ID`/`_SECRET` are set; OpenFEC needs `FEC_API_KEY`. `scripts/check_providers.py [--probe]` (`make check-providers`) inventories every provider + its reachability.

### PPI v1.5 pipeline + `/v15` frontend — `app/pipeline_v15/`, `web/`

The 10-stage twice-daily orchestrator (spec §41): `run_v15_pipeline` chains discover → market snapshot → political data → validate → quant → evidence bundle → blind → ensemble → comparison → publish, records one `JobRun` (`ppi-v15-daily`), runs each race's stages in a SAVEPOINT, and is idempotent per run_key (`as_of` derived from the key). `market_discovery.discover_and_bind` auto-classifies Polymarket contracts and binds `SUPPORTED_STATEWIDE_RACE` ones to `races` (+ `market_yes_party`); everything else → append-only `market_classifications` QUARANTINED, never a fabricated Quant number. `comparison.join_forecasts_with_market` (stage 9) writes `forecast_market_comparisons` with `market_model_spread = market − fair_value`, oriented, after persistence. `cutover.headline_series()` reads `PPI_HEADLINE_SERIES` (default `legacy_blind_llm`) — **the public headline is NOT flipped**; the switch is a one-line config change gated on `docs/research/PPI_CUTOVER.md`. CLI `scripts/run_v15_daily.py` (`--offline`/`--blind-stub`/`--discover`); workflow `.github/workflows/ppi-v15-daily.yml` (gated on `PPI_V15_ENABLED=true`, runs alongside `ppi-daily.yml`); export `scripts/export_v15_bundle.py` → `web/public/data/v15/` (additive; never touches `export_public_bundle.py`). Frontend: `/v15` (spread-sorted table), `/v15/race/:id` (full breakdown), System-Status v1.5 section, methodology rewrite; v1.5 data is load-optional. `npm run check` + `npm run build` pass. New table: `market_classifications`. Detail: `docs/research/PPI_V15_PIPELINE.md`.

### PPI scoring / calibration / backtesting — `app/eval/`

Resolved-outcome evaluation (spec 34-36, 47, 49). `score_resolved_race` scores every series (`market/quant/openai/anthropic/ensemble/legacy_llm`, oriented to `P(contract_yes_party wins)`) at horizons 90/60/30/14/7/1 using the **latest observation at or before `election_date − h days`** — no lookahead — and upserts idempotent `forecast_scores` rows. `build_calibration_report` groups by series/horizon/office/state/version with N + `low_confidence` always shown, plus paired Brier comparisons (`ensemble_vs_quant`, `quant_vs_market`, …) and partisan asymmetry. `leadlag_analysis` classifies which of two series moves first. `run_backtest` (`scripts/ppi_backtest.py --cycle 2026 [--strict]`) re-runs Quant at past point-in-time cutoffs behind a `PointInTimeGuard` that drops/raises on any future poll, excludes cycle-C's own presidential result, and never feeds the resolution into the forecast input. Additive: `races.contract_yes_party` (default DEM) + `races.market_yes_party` (nullable → market/legacy series excluded from that race's scoring). `make score` / `make backtest` / `make eval-test`. Detail: `docs/research/PPI_SCORING_BACKTEST_V1.md`.

### PPI blind benchmarks + v1.5 ensemble — `app/blind/`

GPT + Claude independent blind forecasts (spec 23/24) + the ensemble (spec 25/27). After the Quant forecast and its market-free `EvidenceBundle`, `run_blind_forecasts` sends the bundle (only) to `OpenAIBlindProvider` / `AnthropicBlindProvider` (`claude-opus-5`, adaptive thinking) — **no market price, no Quant probability, no other-model forecast** (asserted). Append-only `blind_benchmark_forecasts` (race-centric, separate from the untouched legacy `llm_forecasts`); missing key/SDK → `SKIPPED_PROVIDER`, never a fabricated value; cost-control reuses an `OK` row at the same evidence hash. `compute_and_persist_ensemble` blends `0.60·Q + 0.20·GPT + 0.20·Claude`; any missing component → `available = False`, never reweighted. `web_evidence.py` collects contamination-filtered race news for the bundle (not a Quant input). `make quant-shadow-blind` / `--blind-stub`; live calls need `pip install -r requirements-blind.txt` + `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. Detail: `docs/research/PPI_BLIND_BENCHMARKS_V1.md`.

## Forecasting contract

- Default model: `deepseek/deepseek-v4-flash-0731` through OpenRouter (`LLM_PROVIDER=openrouter`). Prior to 2026-08-26 this was `qwen3:8b` through local Ollama; Qwen is now the secondary comparison series (`qwen_provider_config`), still run every cycle from the same evidence.
- Run the model separately for every eligible tracked market on every canonical run.
- The model must not receive Polymarket price, bid, ask, midpoint, spread, volume, liquidity, market-derived ranking, prior PPI gap, or any field that reveals market consensus.
- Construct and persist the blind evidence packet before joining model output to market-price data.
- Record model name, provider, prompt version, generation parameters, prompt/input hash, evidence references, raw response, parsed response, retries, timestamps, and error state.
- Use deterministic generation settings where supported and document any remaining nondeterminism.
- Validate model output against a versioned schema. Retry only under a documented bounded policy.
- A valid model probability is final for that run and publishes automatically once its run is classified canonical -- there is no human approval gate on publication. Humans may only flag a forecast for a genuine data-integrity concern (removing it from public display) and may never edit the numeric primary-model forecast or selectively approve one based on its contents.
- If the model still fails after retries, record a failed or abstained forecast. Never substitute a human value, deterministic fallback, or market price into the primary series.
- Manual or alternative-model estimates may exist only as separately labelled comparison baselines. Never mix them into the primary series.
- Do not silently change the primary model. Any model change starts a clearly versioned series and requires a documented comparison.

## Research integrity and history

- Store timestamps in timezone-aware UTC; display Eastern time where useful.
- Preserve raw source payloads and research history indefinitely unless the user explicitly changes the retention policy.
- Every forecast and price observation must be attributable to a run, code version, prompt version, model, evidence packet, and source retrieval time.
- New twice-daily runs append new immutable observations.
- An exact rerun of the same logical `run_key` must not duplicate observations.
- Never update a published prediction in place. Corrections create a new linked revision and preserve the original.
- The target uniqueness model is `market_id + snapshot_kind + effective_timestamp/run_slot`; one-row-per-day logic is insufficient for twice-daily forecasting.
- Raw percentage-point PPI is canonical. A standardized score is secondary and must have a documented, tested formula; do not invent or change normalization silently.
- Aggregate views must show signed mean PPI, mean absolute PPI, eligible market count, freshness, and methodology. Default to equal weighting unless a documented versioned method replaces it.
- Excluded, cancelled, ambiguous, or invalid markets retain their history but are not scored as normal resolved outcomes.
- Prevent look-ahead bias. Lock each forecast before price comparison and resolution scoring.
- Preregister material methodology, universe, prompt, or scoring changes in version-controlled documentation before using them in the primary experiment.

## Deployment and automation

- The static React site is the public Cloudflare Pages surface.
- Python, database, ingestion, and model execution stay server-side; never expose admin credentials, database credentials, raw private notes, or model endpoints to the browser.
- GitHub-hosted runners cannot call Ollama on a developer laptop. Do not claim the local model is automated in production unless using a self-hosted runner or a separately approved hosted inference service.
- Preferred near-term design: a self-hosted scheduled runner executes both the primary (DeepSeek/OpenRouter) and secondary (Qwen/Ollama) series and writes to the production database; GitHub Actions validates, exports sanitized JSON, builds, and deploys the public site.
- A hosted model fallback must be explicit, versioned, cost-capped, and labelled as a different model series.
- Run twice per day. Store UTC and present Eastern time. Every failed/partial run must be visible in admin diagnostics and the sanitized system-status appendix.

## Local setup and commands

Canonical Python is `3.11.9` until an intentional upgrade updates `.python-version`, CI, tooling, and tests together.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
make init
make seed
```

Local services and primary commands:

```bash
ollama pull qwen3:8b
ollama serve
make daily
make run
make export-public
make test
make validate
make web-install
make web-check
make web-build
```

Use the explicit underlying commands in the `Makefile` when diagnosing failures. Never assume `streamlit run app.py`; the current app entry point is `app/dashboard/streamlit_app.py`.

## Required working style

- Inspect relevant code, tests, schemas, and docs before editing.
- For multi-file or architectural work, state a brief plan first.
- Prefer the smallest coherent change, but restructuring is allowed when required to unify the pipeline or remove a false source of truth.
- For reproducible bugs, add or update a failing regression test before the fix.
- Do not add placeholders, fabricated data, silent fallbacks, swallowed exceptions, or misleading success states.
- Fix related defects discovered in the touched path when safe; report unrelated risks clearly.
- Add database changes through migrations. Back up production data before destructive or nontrivial migrations.
- Never disable TLS verification, add `verify=False`, or suppress certificate warnings as a permanent fix.
- Never read, print, commit, or expose `.env`, credentials, password hashes, tokens, or private database URLs.
- Update affected docs whenever commands, architecture, schemas, methodology, prompts, model choice, or deployment behavior changes.

## Validation gates

Run focused tests while developing, then before declaring completion run:

```bash
make validate
make web-check
make web-build
```

The normal suite must be deterministic and mocked. Live Polymarket/Ollama integration checks must be explicit opt-in tests and must not make the default suite flaky.

Do not claim success when a command could not run. Report the exact command, result, and blocker.

## Git workflow

- Begin meaningful work on a feature branch; inspect the actual default branch instead of assuming its name.
- The deployment workflow currently listens to `main`; do not change or merge the default branch without explicit user direction.
- Local commits and pushing feature branches are allowed.
- Use conventional commits such as `fix:`, `feat:`, `test:`, `docs:`, `refactor:`, and `chore:`.
- Never force-push shared branches, rewrite published history, or merge to the default branch.
- Keep commits focused and leave the working tree understandable.

## Completion report

Every completed task must report:

- files changed;
- behavior changed;
- commands run and their results;
- tests/builds passed or not run;
- migrations or deployment implications;
- remaining risks, assumptions, and follow-up work.
