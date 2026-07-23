# Reality Spread Research Layer v0.2

This layer adds the five research upgrades:

1. Parent-group prompting for multi-option markets
2. Markout analysis: 1-day, 7-day, 30-day price movement
3. Calibration/Brier scoring once markets resolve or move
4. Comparison of raw vs normalized vs group-prompt estimates
5. Error taxonomy

## Files

```text
scripts/run_llm_group_fair_values.py
scripts/build_markouts.py
scripts/build_calibration_scores.py
scripts/compare_estimate_modes.py
scripts/build_error_taxonomy.py
scripts/run_research_layer.py
prompts/group_fair_value_prompt_v0_1.md
docs/research_layer_README.md
```

## Required inputs

You should already have:

```text
data/signal_inputs/signal_input_latest.csv
data/llm_estimates/llm_estimates_latest.csv
data/signals/signal_comparison_latest.csv
data/snapshots/signal_input_snapshots.csv
data/snapshots/signal_comparison_snapshots.csv
```

No new API keys are required. Group prompting uses your existing Ollama setup.

## 1. Group-level fair values

Test with mock:

```bash
PYTHONPATH=. python scripts/run_llm_group_fair_values.py --mock --limit-groups 2
```

Real run:

```bash
PYTHONPATH=. python scripts/run_llm_group_fair_values.py   --input data/signal_inputs/signal_input_latest.csv   --model qwen3:8b
```

Outputs:

```text
data/llm_group_estimates/llm_group_estimates_latest.csv
data/snapshots/llm_group_estimate_snapshots.csv
data/health/latest_llm_group_estimate_health.json
```

Use this for multi-option markets where row-by-row probabilities are incoherent.

## 2. Compare raw vs normalized vs group-prompt

```bash
PYTHONPATH=. python scripts/compare_estimate_modes.py
```

Outputs:

```text
data/analysis/estimate_mode_comparison_latest.csv
data/analysis/estimate_mode_group_summary_latest.csv
data/health/latest_estimate_mode_comparison_health.json
```

## 3. Markouts

```bash
PYTHONPATH=. python scripts/build_markouts.py
```

Outputs:

```text
data/markouts/markouts_latest.csv
data/markouts/markouts_<timestamp>.csv
data/health/latest_markout_health.json
```

Early on, 7d/30d rows will be pending. That is expected.

## 4. Calibration / Brier scoring

Soft interim scoring using future market prices:

```bash
PYTHONPATH=. python scripts/build_calibration_scores.py --mode soft
```

True resolution scoring later:

```bash
PYTHONPATH=. python scripts/build_calibration_scores.py   --mode resolution   --resolutions data/resolutions.csv
```

`data/resolutions.csv` should have:

```csv
tracking_id,resolved_outcome,resolved_at
RSO-0001,1,2026-11-04T00:00:00Z
```

## 5. Error taxonomy

```bash
PYTHONPATH=. python scripts/build_error_taxonomy.py
```

Outputs:

```text
data/analysis/error_taxonomy_latest.csv
data/analysis/error_taxonomy_summary_latest.csv
data/health/latest_error_taxonomy_health.json
```

Classes include:

```text
clean_binary_disagreement
use_group_prompt_or_normalized_only
diagnostic_only_incomplete_market
liquidity_caution
multi_option_interpretable_with_normalization
no_signal
standard_signal
```

## One-command research layer

Mock smoke test:

```bash
PYTHONPATH=. python scripts/run_research_layer.py --mock-group --limit-groups 2
```

Real:

```bash
PYTHONPATH=. python scripts/run_research_layer.py --model qwen3:8b
```

## Interpretation rule

Do not treat every large gap as alpha.

Use:

```text
binary clean markets -> raw/normalized direct interpretation
multi-option markets -> group-prompt estimate preferred
incomplete groups -> diagnostic only
thin/ask-only markets -> downweight
markout-supported signals -> stronger evidence
```
