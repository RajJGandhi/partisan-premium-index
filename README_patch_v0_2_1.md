# Reality Spread Patch v0.2.1

Fixes two research-layer issues:

1. `build_markouts.py` returned `Rows: 0` because it expected `raw_signal_direction` / `normalized_signal_direction`, while your signal file uses `raw_direction` / `normalized_direction`.
2. `build_error_taxonomy.py` left `selected_direction` and `selected_strength` blank for the same reason.

## Copy in

```bash
cd /Users/raj/PycharmProjects/Reality_Spread/reality-spread
unzip -o ~/Downloads/reality_spread_research_layer_patch_v0_2_1.zip -d .
```

## Rerun

```bash
PYTHONPATH=. python scripts/build_markouts.py   --signals data/snapshots/signal_comparison_snapshots.csv   --prices data/snapshots/signal_input_snapshots.csv
```

Expected now: not `Rows: 0`. It should produce PENDING rows until future snapshots exist.

Then:

```bash
PYTHONPATH=. python scripts/build_error_taxonomy.py   --signals data/signals/signal_comparison_latest.csv   --markouts data/markouts/markouts_latest.csv
```

Expected now: `selected_direction` should not be blank.

Calibration will still be mostly empty until markouts become READY, but it should no longer be blocked by the zero-row markout bug.
