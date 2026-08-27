# PPI Quant v1.0 -- deterministic quantitative election forecast

**Status:** implemented in `app/quant/`, running in **shadow mode** only. It does **not** yet
replace the headline series. The legacy blind-LLM series (`llm_forecasts`, `raw_ppi = market -
llm_fair_value`) is unchanged and remains canonical. This document is the methodology of record for
the new engine and is written to be checkable without trusting PPI.

Methodology version: **`ppi-quant-v1.0`** (`app.quant.config.METHODOLOGY_VERSION`).
Ensemble version: **`ppi-ensemble-v1.5`**.
Every stored `quant_forecasts` row carries `methodology_version` + `config_hash`
(`MethodologyConfig.config_hash()`), the `input_hash`, and the `evidence_bundle_hash`.

---

## 1. What changed

The old system was:

> evidence -> LLM -> arbitrary probability

PPI Quant is:

> structured political data -> quantitative election model -> margin distribution -> Phi(mu/sigma) -> fair value

There is no code path anywhere in `app/quant/` that says "Democrats seem favored, therefore 72%".
The probability is a deterministic function of explicit assumptions. LLMs are not involved in the
Quant number at all; they return later (next phase) only as *independent blind benchmarks*
(`forecast_role = gpt_blind_benchmark` / `claude_blind_benchmark` on `llm_forecasts`) and as
robustness inputs to the ensemble.

---

## 2. Pipeline (all in vote-margin space, Democratic minus Republican points)

| Step | Module | Output |
|---|---|---|
| Weighted polling average | `app/quant/polling.py` | `polling_margin`, `n_eff` |
| Historical state lean | `app/quant/state_lean.py` | `state_lean` |
| National environment (generic ballot) | `app/quant/national_environment.py` | `national_environment` |
| Fundamentals | `app/quant/fundamentals.py` | `fundamental_margin` |
| Blend | `app/quant/blend.py` | `poll_weight` (alpha), `expected_margin` (mu) |
| Uncertainty | `app/quant/uncertainty.py` | `sigma_time/polling/office/status`, `sigma_total` |
| Margin -> probability | `app/quant/probability.py` | `p_dem_win` (capped + uncapped) |
| Data-quality label | `app/quant/data_quality.py` | STRONG / NORMAL / THIN / DEGRADED |
| Orchestration | `app/quant/engine.py` | `QuantForecastResult` |
| Contract routing | `app/quant/adapters.py` | statewide_race / senate_control / house_control / unsupported |
| Evidence packet | `app/quant/evidence_bundle.py` | immutable, market-free `EvidenceBundle` + hash |
| Ensemble + robustness | `app/quant/ensemble.py` | `EnsembleResult` (or "unavailable") |
| Chamber control | `app/quant/senate_control.py` | Monte Carlo `P(control)` |

`D+5 -> +5.0`, `R+5 -> -5.0`. `Margin = Dem - Rep` everywhere.

---

## 3. Formulas and constants (all provisional -- `MethodologyConfig`)

### 3.1 Historical state partisan lean (section 9 of the spec)

    StateLean_y = StatePresidentialMargin_y - NationalPresidentialMargin_y
    StateLean   = 0.15*L_2016 + 0.30*L_2020 + 0.55*L_2024

If a year is missing from the state or national series its weight is renormalised across the
present years (reported in `detail["redistributed"]`). No history -> `state_lean = None` (not 0).

### 3.2 National political environment (section 10)

Weighted mean of `(Dem_i - Rep_i)` over generic-ballot polls, using the same weight framework as
race polls. No prediction-market data. Empty and no provider override -> `None` (not 0).

### 3.3 Per-poll weight (section 11)

    W_i = Recency_i * Sample_i * Population_i * Quality_i * Sponsor_i * Flooding_i

| Factor | Rule | v1.0 value |
|---|---|---|
| Recency | `0.5 ** (age_days / half_life)` | half-life **21 d** |
| Sample | `sqrt(N / 600)`, floored at N=100, capped at N=5000 | ref 600 |
| Population | lookup | LV 1.00 / RV 0.90 / A 0.75 / Unknown 0.85 |
| Quality | external grade bucket | A 1.10 / B 1.00 / C 0.85 / Unknown 0.90 |
| Sponsor | public / partisan-sponsored / internal | 1.00 / 0.80 / 0.75 |
| Flooding | within 14 d, a pollster's k-th newest poll x `0.5**k` | window 14 d, decay 0.5 |

Partisan/internal polls are **down-weighted, never dropped**, and the multiplier is stored
per-poll in `PollingAverage.per_poll[i]["weight_breakdown"]`.

### 3.4 Polling margin + effective count (section 12)

    PollingMargin = sum(W_i * M_i) / sum(W_i)
    n_eff         = (sum W_i)^2 / sum(W_i^2)

Also stored: raw poll count, used poll count, latest poll date, weighted average poll age,
pollster diversity.

### 3.5 Fundamentals (section 13)

    Senate  : FundamentalMargin = StateLean + NationalEnvironment + Incumbency
    Governor: FundamentalMargin = StateLean + 0.65*NationalEnvironment + Incumbency

Incumbency (Dem-margin points): Senate +/-1.5, Governor +/-2.0, open seat 0.
Missing NationalEnvironment contributes 0 but is flagged (`national_environment_missing`), not
silently treated as a real zero. Missing StateLean -> no fundamental margin.

### 3.6 Blend (section 14)

    BaseAlpha = 1 - e^(-n_eff / 2.5)
    alpha     = min(BaseAlpha, time_cap) * staleness_multiplier
    mu        = alpha*PollingMargin + (1-alpha)*FundamentalMargin

Time caps on alpha: `>180 d -> 0.65`, `91-180 -> 0.75`, `31-90 -> 0.88`, `0-30 -> 0.93`.
Staleness (newest usable poll age): `<=30 d -> x1.0`, `31-60 -> x0.65`, `>60 -> x0.35`.
No usable polls -> `alpha = 0` (fundamentals only). No fundamentals -> `alpha = 1` (polls only).

### 3.7 Uncertainty (section 15)

    sigma_total = sqrt(sigma_time^2 + sigma_polling^2 + sigma_office^2 + sigma_status^2)

- `sigma_time`: continuous linear interpolation of `{365:9.0, 180:8.0, 120:7.0, 90:6.3, 60:5.5,
  30:4.8, 14:4.2, 7:3.8, 1:3.3}`, clamped outside the range.
- `sigma_polling`: `n_eff>=7 -> 0.0`, `>=4 -> 0.5`, `>=2 -> 1.25`, `>=1 -> 2.5`, else `4.0`.
- `sigma_office`: Senate 0.0, Governor 0.75.
- `sigma_status`: `+2.0` (rss) if nominees unconfirmed; `+ up to 1.5` (rss, scaled) if candidate
  mapping confidence is in `[0.60, 1.0)`. Below 0.60 the engine **abstains** instead.

Every component is stored separately (`quant_forecasts.sigma_*`).

### 3.8 Margin -> probability (section 16)

    P(DemWin) = Phi(mu / sigma_total)          Phi = standard normal CDF (math.erf, no SciPy)
    P(RepWin) = 1 - P(DemWin)

Published probability is clamped to `[0.005, 0.995]`; the uncapped value is retained
(`p_dem_win_uncapped`). Nothing is rounded internally.

---

## 4. Data-quality states (section 30)

`STRONG` (>=4 recent polls, n_eff>=3, >=3 pollsters, known nominees, generic ballot, clean
metadata) / `NORMAL` / `THIN` (<=1 usable poll or newest poll >60 d old -> fundamentals dominate)
/ `DEGRADED` (provider on fallback, stale national environment, or mapping confidence 0.60-0.85) /
`ABSTAIN`. Abstention gates (engine, before any calculation):

- candidate mapping confidence `< 0.60`;
- neither contest side identifiable (no candidate metadata);
- no usable polls **and** no state history **and** no national environment.

An abstained forecast stores reasons and `p_dem_win = None` -- never a fabricated number.

---

## 5. Separation from prediction-market information (sections 17, 18, 22, 52)

- `run_quant_forecast(inp, cfg)` has **no market-price parameter**. Enforced by
  `tests/test_quant_market_independence.py` via `inspect.signature`.
- Nothing under `app/quant/` imports `app.ppi.polymarket`, `app.ingest.polymarket_*`,
  `app.ingest.kalshi/predictit`, or `MarketSnapshot` (static AST test).
- `QuantForecastInput.__post_init__` and the engine call `assert_market_free`, which recursively
  rejects ~35 forbidden keys (`market_probability`, `yes_best_bid`, `spread`, `volume`,
  `liquidity`, `last_trade_price`, `raw_ppi`, ...). `EvidenceBundle.build` runs the same check.
- `market_model_spread` is computed only in `forecast_market_comparisons`, by a separate step,
  strictly after the forecast row is persisted. The one place in `app/quant/` that reads a market
  probability at all is `ensemble.robustness_band`, which runs post-hoc and never feeds a forecast.

The canonical observed quantity is **`market_model_spread = MarketProbability - PPIFairValue`**, an
observation -- not proof of partisan bias. See `forecast_market_comparisons.market_model_spread`.

---

## 6. Ensemble + robustness (sections 25, 27)

    PPI_ensemble = 0.60*Quant + 0.20*GPT_blind + 0.20*Claude_blind      (predeclared, not re-fit)

If any component is missing the ensemble is recorded `available = False` with a reason and the
present components are **not reweighted** (`ensemble_forecasts.available`, `unavailable_reason`).
In this phase GPT/Claude are not wired, so every shadow ensemble row is explicitly unavailable.

Robustness (needs a market probability for context, computed after persistence):

- **HIGH**: `|market - ensemble| >= 10 pts` AND max pairwise model disagreement `<= 8 pts`
- **MEDIUM**: max pairwise disagreement `<= 15 pts`
- **LOW**: the gap is mostly PPI's own models disagreeing (e.g. Quant 45 / GPT 72 / Claude 61)

`dispersion = pstdev(quant, gpt, claude)`.

---

## 7. Forecast adapters (section 4)

| Contract type | Adapter | Status |
|---|---|---|
| `statewide_race` | `StatewideRaceAdapter` | **SUPPORTED** (Senate + Governor general elections) |
| `senate_control` | `SenateControlAdapter` | **EXPERIMENTAL** -- Monte Carlo over race distributions; UNAVAILABLE with an empty race set |
| `house_control` | `HouseControlAdapter` | **UNAVAILABLE** -- no fabricated district-level number is shipped |
| anything else | `UnsupportedAdapter` | **ABSTAIN** |

`app.quant.senate_control.simulate_senate_control` runs >=50,000 sims by default, deterministic
given `seed`, draws each race margin from `Normal(mu, sigma_total)`, adds holdover seats, applies
the 50-50 VP tie-break, and optionally a shared `correlated_national_error_sd` term (default 0.0 =
independent races, v1.2 behaviour).

---

## 8. Schema (append-only; `app/db/models_quant.py`)

New tables: `races`, `race_candidates`, `poll_observations`,
`national_environment_observations`, `historical_election_results`, `candidate_status_snapshots`,
`data_provider_runs`, `quant_evidence_bundles`, `quant_forecasts`, `ensemble_forecasts`,
`forecast_market_comparisons`, `forecast_resolutions`, `forecast_scores`, `methodology_versions`,
`provider_health`. `llm_forecasts` gains `methodology_version` (default
`ppi-v0-legacy-blind-llm`) + `forecast_role` (default `legacy_blind_llm`).

Immutability (`app/quant/append_only.py`): `quant_forecasts` / `ensemble_forecasts` are
append-only per `(race_id, run_key, methodology_version, revision)`. A re-run is a no-op
(`upsert_*` returns the existing row). A correction is a **new** row (`revision += 1`,
`correction_of_id` set); the original is never edited. `flag_integrity` sets only
`integrity_flag`/`integrity_note` (suppresses public display) and never touches a numeric field.
`methodology_versions` is write-once per `version`.

Migration: `python scripts/migrate_db.py` (idempotent; `create_all` builds the new tables,
`ADDITIVE_COLUMNS` backfills the two `llm_forecasts` labels via DEFAULT).

---

## 9. Sanity invariants (section 17 -- `tests/test_quant_invariants.py`)

- **Symmetry**: mirror every Dem quantity to its Rep image (poll dem<->rep, lean/environment
  negated, incumbency swapped) => `P(D)' == 1 - P(D)`, `mu' == -mu`, `sigma` unchanged.
- **Monotonicity**: raising the Dem polling margin never lowers `P(D)`; shrinking sigma with
  `mu > 0` never lowers `P(D)`; adding more polls at the same lead never lowers `P(D)` when ahead.
- **Strong-lead sanity**: polling ~D+8, fundamentals ~D+2, many recent polls, ~75 d out =>
  `P(D) > 0.80` (not an absurd ~0.42).
- **Toss-up sanity**: `expected_margin ~ 0` => `P(D)` in `[0.42, 0.58]`.
- **Market independence**: the forecast function has no market argument; forbidden keys are
  rejected; the evidence bundle and public dict contain no market fields.

`make quant-test` runs the 115-test Quant suite (no DB, no network). `make quant-shadow` /
`quant-shadow-dry` run the engine against `data/seed/quant_example_races.json`.

---

## 10. Explicitly deferred (later phases, not in this change)

- **Data-acquisition layer**: DONE — see `docs/research/PPI_PROVIDERS_V1.md`. `app/providers/`
  now supplies `ElectionHistoryProvider` / `GenericBallotProvider` / `PollProvider` /
  `CandidateProvider` / `MarketDiscoveryProvider` behind `BaseProvider` + `ProviderChain` with
  caching, retry/backoff, last-known-good (`provider_cache`), `provider_health`, `data_provider_runs`
  audit rows, race/candidate matching, and the prediction-market contamination scanner. Concrete
  adapters: Decision Desk HQ Polling API (verified), VoteHub / PollingSource / DDHQ-results
  (approximate shape, mocked tests), OpenFEC + Polymarket Gamma (reuse existing modules). `make
  ingest` populates the DB; `scripts/run_quant_shadow.py --from-db` runs providers → DB → engine.
- **GPT + Claude blind benchmark runners** and the real ensemble: DONE — see
  `docs/research/PPI_BLIND_BENCHMARKS_V1.md`. `app/blind/` runs both frontier models on the
  market-free `EvidenceBundle` (append-only `blind_benchmark_forecasts`), then
  `compute_and_persist_ensemble` blends Quant + GPT + Claude with the predeclared `0.60/0.20/0.20`
  weights (`available = False`, no reweight, when a component is missing). `make quant-shadow-blind`
  / `--blind-stub`. Live calls need `requirements-blind.txt` + keys.
- **Scoring / calibration / backtesting**: DONE — see `docs/research/PPI_SCORING_BACKTEST_V1.md`.
  `app/eval/` scores every series (`market/quant/openai/anthropic/ensemble/legacy_llm`) at the
  standard horizons using the nearest observation **without lookahead**, aggregates calibration +
  paired Brier comparisons, does lead/lag, and runs a point-in-time backtest
  (`ppi_backtest.py --cycle …`, `--strict`) behind a `PointInTimeGuard` that drops/raises on any
  future datum and never feeds the resolution into the forecast. `make score` / `make backtest`.
- **Frontend + 10-stage scheduler**: DONE (shadow) — see `docs/research/PPI_V15_PIPELINE.md`.
  `app/pipeline_v15/orchestrator.py` chains discover → market snapshot → political data → validate
  → quant → evidence bundle → blind → ensemble → comparison → publish (one `JobRun`, SAVEPOINT per
  race, idempotent per run_key); `market_discovery` auto-classifies/binds Polymarket contracts
  (AMBIGUOUS → `market_classifications` quarantine); `comparison` writes
  `forecast_market_comparisons` (`market_model_spread`). `scripts/run_v15_daily.py`,
  `.github/workflows/ppi-v15-daily.yml`, `scripts/export_v15_bundle.py` → `web/public/data/v15/`,
  and the `/v15` + `/v15/race/:id` React pages + System Status provider rows + the methodology
  rewrite. The public **headline is not flipped** — gated on `docs/research/PPI_CUTOVER.md`
  (`PPI_HEADLINE_SERIES`, `app/pipeline_v15/cutover.py`).
- **Real historical dataset**: `data/seed/historical_presidential_national.csv` carries only the
  three national margins as a bootstrap; the full 50-state + DC series comes from the
  ElectionHistoryProvider.
- **House district-level model** and **correlated Senate national error** activation.

---

## 11. Provisional status (section 48)

Every constant in section 3 is **provisional** -- transparent, defensible, not backtested.
`app.quant.config.PROVISIONAL_PARAMETERS` is the machine-readable catalogue (surfaced on the
methodology page later). The goal of v1.0 is *transparent, deterministic, auditable, falsifiable*
-- not calibrated-from-day-one. Later versions estimate these from resolved out-of-sample
performance, with a methodology-version bump, a frozen evaluation window, and no retroactive
rewriting.
