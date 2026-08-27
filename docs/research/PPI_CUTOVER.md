# PPI headline-series cutover — decision record

**Status:** the public headline PPI fair value is still **`legacy_blind_llm`**
(`raw_ppi = market − llm_fair_value`, `app/ppi/blind_forecast.py` → `app/ppi/public_forecast.py`).
The v1.5 quantitative pipeline runs in **shadow** and is exposed on the public `/v15` page as a
clearly-labelled experimental series. It does **not** feed the headline index, aggregates, or the
existing `markets` export.

Flipping the headline is a one-line change (`PPI_HEADLINE_SERIES=quant` or `=ensemble`) once this
checklist is satisfied and a dated decision is appended to this document. Nothing about the flip
is automatic (spec §25, §50 Phase E).

---

## The switch

- `app.config.Settings.ppi_headline_series` — `legacy_blind_llm` (default) | `quant` | `ensemble`.
- `app.pipeline_v15.cutover.headline_series()` reads it; `headline_forecast(session, race_id)`
  returns the current public fair value for the configured series (`None` for `legacy_blind_llm`,
  which the unchanged legacy path serves).
- `scripts/export_v15_bundle.py` stamps `headline_series` into every v1.5 file so the web app can
  say plainly whether the numbers it shows are the headline or a shadow series.

## Validation checklist (all must hold before the flip)

1. The v1.5 pipeline (`ppi-v15-daily.yml` / `scripts/run_v15_daily.py`) has run twice daily for
   **≥ 30 canonical cycles** with no unexplained failures (`job_runs` where `job_name =
   'ppi-v15-daily'`).
2. A `quant_forecasts` row **and** an `ensemble_forecasts` row exist for every enabled supported
   race on every one of those cycles (or an explicit, recorded ABSTAIN).
3. The invariant tests (`tests/test_quant_invariants.py`: symmetry, monotonicity, strong-lead,
   toss-up) pass against the **live** methodology config, not just the frozen v1.0 defaults.
4. `forecast_market_comparisons` has recorded `market_model_spread` for the tracked races for
   **≥ 2 weeks**, so the lead/lag and calibration views have real observation history.
5. **≥ 1 resolved race** has been scored for every series (`market / quant / openai / anthropic /
   ensemble / legacy_llm`) via `score_resolved_race` with no point-in-time leak.
6. `provider_health` shows no series stuck `DEGRADED` / `DOWN` for any acquisition path the Quant
   forecast depends on (polls, generic ballot, election history).
7. Backtests for at least the prior cycle (`scripts/ppi_backtest.py --cycle <prev> --strict`)
   complete with no `PointInTimeError` and a mean Brier at each horizon that is not worse than the
   legacy series on the overlapping resolved set.
8. A **dated entry below** records the decision, the series chosen (`quant` or `ensemble`), the
   Brier/calibration evidence it rests on, and who signed off.

`app.pipeline_v15.cutover.cutover_readiness(session)` surfaces an advisory snapshot (counts +
this checklist) on the System Status page. It is not authoritative — item 8 is.

## After the flip

- The legacy series is **retained** and stays visible (labelled "Legacy blind-LLM / PPI v0"); its
  history is never deleted or rewritten.
- `methodology_versions` records the config in force at the moment of the flip.
- A follow-on obligation: continue scoring the legacy series so the "did switching help?" question
  stays answerable.

---

## Decisions

*(none yet — the headline remains `legacy_blind_llm`)*
