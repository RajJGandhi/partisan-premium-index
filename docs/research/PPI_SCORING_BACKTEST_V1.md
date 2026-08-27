# PPI scoring, calibration, lead/lag + backtesting v1 (Phase I)

**Status:** implemented in `app/eval/`. Every function is offline (no network); the default test
suite exercises it against the synthetic seed races. Shadow-only — nothing here changes the
headline series.

Answers the spec §49 research questions: are markets better calibrated than PPI Quant? are
GPT/Claude? does the ensemble lower Brier? when the models agree against the market, who's right?
are spreads different when the priced outcome favours D vs R? does divergence shrink near
resolution / with liquidity?

---

## 1. Metrics (`app/eval/metrics.py`) — pure

`brier(p, y)` = `(p−y)²`; `log_loss(p, y)` with `p` clamped to `[1e-15, 1−1e-15]` so 0/1 forecasts
stay finite; `party_direction_error` (1 if the forecast is on the wrong side of 0.5);
`calibration_bins` (10 bins, only populated bins reported, mean-predicted vs observed-rate);
`aggregate(pairs)` → `n, mean_brier, mean_log_loss, mean_predicted, resolution_rate,
direction_error_rate, calibration_error, low_confidence (n < 20), bins`. **N is always reported;
nothing claims significance on a small sample.** `STANDARD_HORIZONS = (90, 60, 30, 14, 7, 1)`.

## 2. Series collection (`app/eval/series.py`)

`collect_series(session, race_id)` → `{series → [Observation]}` for
`market / quant / openai / anthropic / ensemble / legacy_llm`, time-ordered, one point per run.

- Every probability is oriented to **P(`race.contract_yes_party` wins)** (default `DEM`). Quant
  `p_dem_win`, the blind prompt, and `resolutions.dem_won` are already in that space.
- `market` and `legacy_llm` are flipped using `race.market_yes_party`; if that is **unknown the
  series is dropped for that race** (abstain rather than guess a direction).
- Abstained Quant rows, non-`OK` blind rows, and unavailable ensemble rows are excluded.

## 3. Scoring (`app/eval/scorer.py`)

`score_resolved_race(session, race_id)` — for a race with a `forecast_resolutions` row, for each
standard horizon `h`:

- target timestamp = `election_date − h days` (end of that day, UTC);
- observation used = **the latest observation at or before the target** (`nearest_observation`) —
  a 30-days-before score never sees a poll, a model run, or an outcome from inside the final 30
  days; a horizon with no pre-target observation is simply not scored;
- upsert a `forecast_scores` row `(race_id, series, horizon_days)` with `brier` + `log_loss`.
  Scores are a pure function of immutable forecasts + an immutable resolution, so **re-scoring is
  idempotent** (existing row updated to the identical recomputed value; no duplicate rows).

`score_all_resolved(session)` scores every resolved race. `record_resolution` /
`load_resolutions_file` (`app/eval/resolutions.py`) ingest outcomes write-once (a correction is an
explicit `allow_correction=True` call, stamped).

## 4. Calibration + comparisons (`app/eval/calibration.py`)

`build_calibration_report(session, group_by=("series", "horizon_days"), …)` — aggregates
`forecast_scores` joined to `races`. Group dimensions: `series`, `horizon_days`, `office`, `state`,
`methodology_version`. Every group and the overall carry `n` + `low_confidence`.

`comparisons` (paired **by race_id + horizon** so only like-for-like observation distances are
compared): `ensemble_vs_quant`, `quant_vs_market`, `ensemble_vs_market`, `openai_vs_quant`,
`anthropic_vs_quant` — each `{n, mean_brier_delta (negative ⇒ first series better), a_better_share,
low_confidence}`. Plus `partisan_asymmetry`: per-series mean signed error `(p − y)` split by
whether the forecast favoured YES vs NO.

## 5. Lead / lag (`app/eval/leadlag.py`)

`leadlag_analysis(series_a_obs, series_b_obs, max_lag=3, min_points=6)` — cross-correlates the two
series' daily first-differences at integer lags; classifies `A_leads` / `B_leads` / `synchronous`
(among lags within tolerance of the peak `|corr|`, the one closest to 0 wins) /
`insufficient_data`. Exploratory — no significance claim on a short series.

## 6. Backtesting (`app/eval/backtest.py`, `scripts/ppi_backtest.py`)

```bash
make backtest                                        # seeded 2026 cycle
PYTHONPATH=. python scripts/ppi_backtest.py --cycle 2026 --model ppi-quant-v1.0 --strict
PYTHONPATH=. python scripts/ppi_backtest.py --config path/to/races.json --json report.json
```

For each race, for each horizon `h`, `run_backtest` sets `as_of = election_date − h days` and
builds a `QuantForecastInput` behind a **`PointInTimeGuard`**:

- a poll conducted after the cutoff (`end_date > as_of`) is **dropped** (`--strict` raises
  `PointInTimeError` instead) — `dropped_future_data` is reported per point;
- cycle-C's own presidential result is excluded from state lean (`year < cycle`);
- the resolution / final margin is used **only to score the model's output**, never fed into
  `_build_pit_input` — proven by `test_backtest_never_reads_the_resolution_or_a_later_poll`
  (forecasts are byte-identical with and without a resolution supplied).

`BacktestReport` gives per-race-per-horizon forecast + Brier + `abstained` + `dropped_future_data`,
and `by_horizon` aggregate (`n`, `mean_brier`, `abstain_rate`). Works today on a races config
(inline polls / history / generic ballot / resolution); the same guard wraps the live provider
chains once real historical datasets are wired.

## 7. Schema

No new tables — `forecast_scores` and `forecast_resolutions` were created by the PPI Quant v1.5
migration. Two additive columns on `races`: `contract_yes_party` (default `DEM`) and
`market_yes_party` (nullable — set during market discovery; NULL ⇒ market/legacy series excluded
from that race's scoring). `python scripts/migrate_db.py` is idempotent and additive.

## 8. Results on the seed data (illustrative, stub-driven)

```
make backtest   → by_horizon mean Brier ≈ 0.13–0.15 across 90/60/30/14/7/1 (3 synthetic races)
                  xx-sen h=90: 3 future polls dropped → THIN; h=7: 0 dropped
make score      → 60 forecast_scores rows (no 90-day: all shadow forecasts post-date election−90d)
                  per-series mean Brier + direction_error_rate 0.0; ensemble_vs_quant Δ ≈ −0.016 (n=15, low_confidence)
```

## 9. Deferred

- **Live resolution ingestion** from an election-results provider (the interface + write-once
  store are built; the automated fetch is not).
- **Lead/lag persistence + a report surface** — the analysis is a pure function; nothing stores
  its output yet.
- **Frontend** calibration curves / track-record page (Phase H); the **10-stage scheduler**
  running score-on-resolve (Phase E).
- Real multi-cycle historical datasets for `--cycle 2024` / `--cycle 2022` (framework + guard
  ready; needs the `ElectionHistoryProvider` populated back that far).
