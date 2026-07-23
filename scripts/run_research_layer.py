#!/usr/bin/env python3
"""
scripts/run_research_layer.py

Runs the post-v0.1 research layer.

This does NOT rerun order books or row-level LLM estimates.
It assumes these already exist:
    data/signal_inputs/signal_input_latest.csv
    data/llm_estimates/llm_estimates_latest.csv
    data/signals/signal_comparison_latest.csv

It can run:
    - group-level LLM estimates
    - estimate mode comparison
    - markouts
    - soft calibration scoring
    - error taxonomy

Run:
    PYTHONPATH=. python scripts/run_research_layer.py --mock-group --limit-groups 2

Then real group estimates:
    PYTHONPATH=. python scripts/run_research_layer.py --model qwen3:8b
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], skip: bool = False) -> None:
    print("\n$ " + " ".join(cmd))
    if skip:
        print("SKIPPED")
        return
    subprocess.run(cmd, check=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Reality Spread research layer.")
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--mock-group", action="store_true")
    p.add_argument("--limit-groups", type=int, default=None)
    p.add_argument("--skip-group", action="store_true")
    p.add_argument("--skip-markouts", action="store_true")
    p.add_argument("--skip-calibration", action="store_true")
    p.add_argument("--skip-taxonomy", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    py = sys.executable

    group_cmd = [
        py,
        "scripts/run_llm_group_fair_values.py",
        "--input",
        "data/signal_inputs/signal_input_latest.csv",
        "--model",
        args.model,
    ]
    if args.mock_group:
        group_cmd.append("--mock")
    if args.limit_groups is not None:
        group_cmd.extend(["--limit-groups", str(args.limit_groups)])

    run(group_cmd, skip=args.skip_group)

    run(
        [
            py,
            "scripts/compare_estimate_modes.py",
            "--signals",
            "data/signals/signal_comparison_latest.csv",
            "--group-estimates",
            "data/llm_group_estimates/llm_group_estimates_latest.csv",
        ]
    )

    run(
        [
            py,
            "scripts/build_markouts.py",
            "--signals",
            "data/snapshots/signal_comparison_snapshots.csv",
            "--prices",
            "data/snapshots/signal_input_snapshots.csv",
        ],
        skip=args.skip_markouts,
    )

    run(
        [
            py,
            "scripts/build_calibration_scores.py",
            "--mode",
            "soft",
            "--signals",
            "data/signals/signal_comparison_latest.csv",
            "--markouts",
            "data/markouts/markouts_latest.csv",
        ],
        skip=args.skip_calibration,
    )

    run(
        [
            py,
            "scripts/build_error_taxonomy.py",
            "--signals",
            "data/signals/signal_comparison_latest.csv",
            "--markouts",
            "data/markouts/markouts_latest.csv",
        ],
        skip=args.skip_taxonomy,
    )

    print("\nResearch layer complete.")


if __name__ == "__main__":
    main()
