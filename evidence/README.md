# Reality Spread Evidence Pack v0.1

As of: 2026-06-26

This evidence pack is designed for the `run_llm_fair_values.py` runner.

## Contents

- `shared/`: common context by region, bucket, and system type.
- `parents/`: detailed evidence packets by underlying event group and parent market name.
- `markets/`: row-specific notes for each tracked option contract.
- `sources/source_index.md`: source index used to build the pack.

## Counts

- Signal universe rows: 188
- Parent market groups: 34
- Shared files: 18
- Parent files: 68
- Row-specific market files: 188

## Usage

Copy this folder to your repo as `evidence/`:

```bash
cp -R evidence_full_v0_1/* evidence/
```

Then run:

```bash
PYTHONPATH=. python scripts/run_llm_fair_values.py \
  --input data/signal_inputs/signal_input_latest.csv \
  --model qwen3:8b
```

## Important methodology rule

These files intentionally contain no Polymarket prices. The LLM should be blind to market price and order-book data when estimating fair value.
