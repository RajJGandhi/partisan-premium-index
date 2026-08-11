---
paths:
  - "app/{llm,ppi,scoring,ingest,db}/**/*.py"
  - "scripts/{run_llm*,run_daily*,run_ppi*,build_signal*,build_calibration*,export_public*,migrate_db.py}"
  - "prompts/**/*.md"
  - "{PPI_METHODOLOGY.md,PPI_ARCHITECTURE.md,PRD.md,docs/**/*.md}"
---

# Research integrity rules

- Keep the primary forecast blind to all prediction-market prices and derived market signals.
- Join prices to forecasts only after the model response is persisted and timestamped.
- Preserve prompt, evidence, raw response, parsed response, model metadata, and hashes for auditability.
- Never edit a valid primary-model probability after generation. A canonical forecast publishes automatically once persisted; data-integrity review may only flag it (suppressing public display), never approve, select, or edit it.
- Treat every methodology, prompt, schema, model, and normalization change as versioned research methodology.
- Append observations and corrections. Never overwrite historical forecasts, prices, evidence, or resolutions.
- Record failures explicitly; absence of data is not zero, neutral, or success.
- Keep the primary Qwen series separate from human, deterministic, mock, group-normalized, or alternative-model baselines.
- Do not infer prelaunch model values when backfilling price history.
- Resolution scoring must use the forecast version that existed at the evaluated timestamp.
- Expose sample size, staleness, exclusions, and limitations with performance metrics.
