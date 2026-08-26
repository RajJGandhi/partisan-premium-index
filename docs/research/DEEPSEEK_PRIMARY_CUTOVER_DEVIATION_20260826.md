# Deviation Record: Early Cutover to DeepSeek/OpenRouter as Primary Series

**This is a deviation record, not a preregistration.** It documents a methodology change made
*without* completing the decision process `docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md`
itself preregistered, per that document's own Section 11 ("Deviations policy") and this project's
`research-integrity.md` rule that "any model change starts a clearly versioned series and requires
a documented comparison." This document is that required record.

| Field | Value |
|---|---|
| Date (UTC) | 2026-08-26 |
| Directed by | Raj, explicitly, in conversation with Claude |
| Repository | `RajJGandhi/partisan-premium-index` |
| Branch | `feat/deepseek-openrouter-primary-cutover` |

## What changed

`PRIMARY_SERIES_PROVIDERS` (`app/ppi/blind_forecast.py`) changed from `{"ollama"}` to
`{"openrouter"}`. The production workflow (`.github/workflows/ppi-daily.yml`) changed
`LLM_PROVIDER` from `ollama` to `openrouter`. `app/ppi/pipeline.py`'s two per-market forecast
calls were swapped: the primary call (counted in `job.llm_forecasts_*`, gated by
`AUTOMATED_PROVIDERS`/`strict_llm_only`) now uses DeepSeek V4 Flash 0731 via OpenRouter; the
secondary comparison call now uses Qwen3-8B via local Ollama (`qwen_provider_config`), the exact
inverse of the arrangement `PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md` described.

No prompt text, evidence pipeline, blindness enforcement, or generation settings changed. Both
series still generate from one shared evidence-collection pass per market per cycle, so matched-
pair comparison data (`is_matched_pair`) keeps accumulating exactly as before, just with the two
providers' roles reversed.

## Why this is a deviation, not a completed cutover

`PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md` Section 18 requires 60 matched comparison cycles before
any promotion decision. At the time of this change, the production database contained **zero**
`openrouter`-provider `LLMForecast` rows (48 `ollama` rows, 0 `openrouter` rows) — the dual-series
production implementation had been merged (PR #14) but no scheduled run had executed it yet. None
of that preregistration's Section 18 decision criteria (reliability gate, discrimination
replication, calibration check) were evaluated before this change, because there was no data to
evaluate them against.

This means the historical basis for calling DeepSeek "the better model" — the entire point of the
preregistered comparison — does not yet exist. This cutover is **not** an evidence-based promotion
under that framework; it is a direct instruction, made for reasons outside the scope of that
document (cost: DeepSeek V4 Flash 0731 is the cheaper model of the pair to run via a hosted API
versus maintaining local Ollama inference), overriding the framework's own gate.

## What is preserved

- `job_run_id=21` and all other historical `ollama`-provider `LLMForecast` rows remain untouched,
  immutable, and queryable exactly as before (`research-integrity.md`: "Never overwrite historical
  forecasts").
- The Qwen/Ollama series continues running every cycle as the new secondary comparison arm, so the
  originally preregistered Qwen-vs-DeepSeek comparison data keeps accumulating — it is simply no
  longer gating anything, and the two series' labels in `PRIMARY_SERIES_PROVIDERS` are reversed
  from what Section 6 of the preregistration described.
- `PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md` and `PPI_V2_PREREGISTRATION.md` are unmodified. They
  remain accurate historical records of what was preregistered and when; they are not updated to
  retroactively describe this cutover.

## What is NOT preserved

- The preregistration's Section 6 "Public visibility during the comparison window" rule — "V1
  remains the sole headline canonical PPI series throughout the entire comparison window" — is
  violated by this change. The headline public PPI series is, as of this cutover, DeepSeek's
  output, not Qwen's, without the 60-cycle comparison window ever completing.

## Follow-up work not performed by this change

- No new `methodology_version`/`evidence_pipeline_version` schema fields were added (these were
  scoped to the separate, still-unimplemented V1-vs-V2 *prompt* preregistration, not this cutover).
- The public site / export schema was not audited for any UI copy that still names Qwen as the
  headline model — flagged for a follow-up pass, not fixed here.
- `docs/OPERATIONS.md`, `docs/SELF_HOSTED_RUNNER.md`, `docs/daily_runner_README.md`,
  `docs/research_layer_README.md`, and top-level `README.md` still reference Qwen as the primary
  model in places; only `CLAUDE.md` (the canonical, living operating doc per its own stated
  authority) was updated as part of this change. These other docs should be reconciled in a
  follow-up pass.
- No live verification run has been executed as part of this change (would create real, immutable
  production data and consume real OpenRouter spend) — recommended before or immediately after
  merging, via `workflow_dispatch` with `slot: adhoc`.
