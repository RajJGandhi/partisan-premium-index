# Reality Spread Patch v0.2.2

This fixes the remaining markout/taxonomy issue.

## What was wrong

Patch v0.2.1 still looked for direction columns. Your signal files have usable `raw_gap` and `normalized_gap`, but the direction fields are absent/blank.

So v0.2.2 infers:

```text
gap >= +0.10  -> LLM_HIGHER
gap <= -0.10  -> MARKET_HIGHER
otherwise     -> NO_SIGNAL
```

## Copy in

```bash
cd /Users/raj/PycharmProjects/Reality_Spread/reality-spread
unzip -o ~/Downloads/reality_spread_research_layer_patch_v0_2_2.zip -d .
```

## Rerun markouts

```bash
PYTHONPATH=. python scripts/build_markouts.py   --signals data/snapshots/signal_comparison_snapshots.csv   --prices data/snapshots/signal_input_snapshots.csv
```

Expected:
- Rows should be greater than 0.
- Most/all rows will be PENDING until future snapshots exist.
- You should see debug counters.

## Rerun taxonomy

```bash
PYTHONPATH=. python scripts/build_error_taxonomy.py   --signals data/signals/signal_comparison_latest.csv   --markouts data/markouts/markouts_latest.csv
```

Expected:
- Selected directions should no longer be blank.
- You should see counts for LLM_HIGHER, MARKET_HIGHER, and NO_SIGNAL.

## Calibration

```bash
PYTHONPATH=. python scripts/build_calibration_scores.py --mode soft
```

It will still score 0 rows until some markouts are READY.
