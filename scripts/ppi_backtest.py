"""Point-in-time backtest for PPI Quant (spec section 47).

    PYTHONPATH=. python scripts/ppi_backtest.py --cycle 2026
    PYTHONPATH=. python scripts/ppi_backtest.py --cycle 2026 --model ppi-quant-v1.0 --strict
    PYTHONPATH=. python scripts/ppi_backtest.py --config path/to/races.json --json report.json

The historical inputs are filtered to each horizon's cutoff -- a poll conducted after the cutoff,
cycle-C's own presidential result, later candidate info, the final margin, and the resolution can
never enter the forecast. ``--strict`` raises on the first lookahead datum instead of dropping it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.backtest import load_backtest_config, run_backtest
from app.eval.metrics import STANDARD_HORIZONS

DEFAULT_CONFIG = Path("data/seed/quant_example_races.json")
DEFAULT_RESOLUTIONS = Path("data/seed/quant_example_resolutions.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest PPI Quant at past point-in-time cutoffs.")
    ap.add_argument("--cycle", type=int, default=None, help="election cycle (default: from the config)")
    ap.add_argument("--model", default=None, help="methodology version label to record (default: current)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="races JSON")
    ap.add_argument("--resolutions", type=Path, default=DEFAULT_RESOLUTIONS, help="outcomes JSON (optional)")
    ap.add_argument("--horizons", default=",".join(map(str, STANDARD_HORIZONS)))
    ap.add_argument("--strict", action="store_true", help="raise on the first lookahead datum")
    ap.add_argument("--json", type=Path, default=None, help="write the full report to this path")
    args = ap.parse_args()

    races, cfg_cycle, resolutions = load_backtest_config(args.config)
    if args.resolutions and args.resolutions.exists() and not resolutions:
        doc = json.loads(args.resolutions.read_text())
        resolutions = {r["race_id"]: r for r in doc.get("resolutions", [])}
    cycle = args.cycle or cfg_cycle
    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]

    report = run_backtest(
        races, cycle=cycle, model_version=args.model, horizons=horizons,
        resolutions=resolutions, strict=args.strict,
    )
    payload = report.as_dict()
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2, default=str))
        payload = {k: payload[k] for k in ("cycle", "model_version", "strict", "n_races", "n_scored", "by_horizon")}
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
