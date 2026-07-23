# Reality Spread Daily Runner

This adds a one-command orchestrator:

```text
scripts/run_daily_experiment.py
```

## Required values before running

No API keys or wallet secrets are needed.

You only need:

```text
Ollama running, unless using --mock-llm / --mock-group
Model installed, default qwen3:8b
Final market file present: data/tracked_markets_final.csv
```

Start Ollama if needed:

```bash
ollama serve
```

Install/pull model if needed:

```bash
ollama pull qwen3:8b
```

## Copy in

```bash
cd /Users/raj/PycharmProjects/Reality_Spread/reality-spread
unzip -o ~/Downloads/reality_spread_daily_runner_v0_1.zip -d .
```

## Smoke test

This does not require Ollama:

```bash
PYTHONPATH=. python scripts/run_daily_experiment.py   --mock-llm   --mock-group   --llm-limit 5   --group-limit 2
```

## Real daily run

```bash
PYTHONPATH=. python scripts/run_daily_experiment.py   --model qwen3:8b
```

## Faster real run without group LLM

```bash
PYTHONPATH=. python scripts/run_daily_experiment.py   --model qwen3:8b   --skip-group-llm
```

## Post-processing only

If you already ran fresh order books and LLM estimates:

```bash
PYTHONPATH=. python scripts/run_daily_experiment.py   --skip-orderbooks   --skip-signal-input   --skip-row-llm   --skip-group-llm
```

## Outputs

The runner writes:

```text
data/runs/daily_run_<run_id>.json
data/runs/latest_daily_run.json
```

And updates all the normal latest files:

```text
data/orderbook_check.csv
data/signal_inputs/signal_input_latest.csv
data/llm_estimates/llm_estimates_latest.csv
data/signals/signal_comparison_latest.csv
data/llm_group_estimates/llm_group_estimates_latest.csv
data/analysis/estimate_mode_comparison_latest.csv
data/markouts/markouts_latest.csv
data/analysis/error_taxonomy_latest.csv
data/scoring/calibration_scores_latest.csv
```

## What to expect early

Markouts will be `PENDING` until future snapshots exist.

Calibration in soft mode will score 0 rows until markouts become `READY`.
