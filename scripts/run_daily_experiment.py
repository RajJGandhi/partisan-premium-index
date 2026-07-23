#!/usr/bin/env python3
"""
scripts/run_daily_experiment.py

One-command daily runner for Reality Spread.

Pipeline:
  1. check_orderbooks.py
  2. build_signal_input_snapshot.py
  3. run_llm_fair_values.py
  4. build_signal_comparison.py
  5. run_llm_group_fair_values.py
  6. compare_estimate_modes.py
  7. build_markouts.py
  8. build_error_taxonomy.py
  9. build_calibration_scores.py --mode soft

Default:
  - Runs the full pipeline.
  - Uses qwen3:8b unless overridden.
  - Stops on first failure.
  - Writes data/runs/daily_run_<run_id>.json.
  - Prints health summaries.

Requirements:
  - Ollama running if not using --mock-llm / --mock-group.
  - No wallet/private key/API secret required.
  - Polymarket CLOB order-book access is read-only.

Smoke test:
  PYTHONPATH=. python scripts/run_daily_experiment.py \
    --mock-llm \
    --mock-group \
    --llm-limit 5 \
    --group-limit 2

Real daily run:
  PYTHONPATH=. python scripts/run_daily_experiment.py \
    --model qwen3:8b

Useful faster run:
  PYTHONPATH=. python scripts/run_daily_experiment.py \
    --model qwen3:8b \
    --skip-group-llm

If you already ran the row LLM estimates and only want post-processing:
  PYTHONPATH=. python scripts/run_daily_experiment.py \
    --skip-orderbooks \
    --skip-row-llm \
    --skip-group-llm
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


HEALTH_FILES = {
    "orderbook": "data/health/latest_orderbook_health.json",
    "signal_input": "data/health/latest_signal_input_health.json",
    "llm_estimate": "data/health/latest_llm_estimate_health.json",
    "signal_comparison": "data/health/latest_signal_comparison_health.json",
    "llm_group_estimate": "data/health/latest_llm_group_estimate_health.json",
    "estimate_mode_comparison": "data/health/latest_estimate_mode_comparison_health.json",
    "markout": "data/health/latest_markout_health.json",
    "error_taxonomy": "data/health/latest_error_taxonomy_health.json",
    "calibration": "data/health/latest_calibration_health.json",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: Optional[dt.datetime] = None) -> str:
    return (ts or utc_now()).isoformat().replace("+00:00", "Z")


def ensure_dir(path: str | Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json(path: str | Path) -> Optional[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc), "_path": str(path)}


def write_json(path: str | Path, obj: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def run_command(
    name: str,
    cmd: List[str],
    *,
    manifest: Dict[str, Any],
    dry_run: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> None:
    print("\n" + "=" * 100)
    print(f"STEP: {name}")
    print("$ " + " ".join(cmd))

    step = {
        "name": name,
        "cmd": cmd,
        "started_at": iso(),
        "ended_at": None,
        "duration_seconds": None,
        "returncode": None,
        "status": "DRY_RUN" if dry_run else "RUNNING",
    }
    manifest["steps"].append(step)

    if dry_run:
        step["ended_at"] = iso()
        step["duration_seconds"] = 0
        return

    t0 = time.time()
    try:
        result = subprocess.run(cmd, check=False, env=env)
        step["returncode"] = result.returncode
        step["ended_at"] = iso()
        step["duration_seconds"] = round(time.time() - t0, 3)

        if result.returncode != 0:
            step["status"] = "FAILED"
            manifest["status"] = "FAILED"
            raise SystemExit(f"Step failed: {name} returncode={result.returncode}")

        step["status"] = "OK"

    except KeyboardInterrupt:
        step["status"] = "INTERRUPTED"
        step["ended_at"] = iso()
        step["duration_seconds"] = round(time.time() - t0, 3)
        manifest["status"] = "INTERRUPTED"
        raise


def summarize_health() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, path in HEALTH_FILES.items():
        data = read_json(path)
        if data is not None:
            out[name] = data
    return out


def print_health_summary(health: Dict[str, Any]) -> None:
    print("\n" + "=" * 100)
    print("HEALTH SUMMARY")

    orderbook = health.get("orderbook", {})
    if orderbook:
        print("\nOrder books")
        for k in ["source_market_rows", "snapshot_token_rows", "status_counts", "ok_rate", "avg_spread", "max_spread"]:
            if k in orderbook:
                print(f"  {k}: {orderbook[k]}")

    signal_input = health.get("signal_input", {})
    if signal_input:
        print("\nSignal input")
        for k in [
            "rows_total",
            "signal_ready_count",
            "skipped_count",
            "signal_ready_rate",
            "price_type_counts",
            "liquidity_flag_counts",
        ]:
            if k in signal_input:
                print(f"  {k}: {signal_input[k]}")

    llm = health.get("llm_estimate", {})
    if llm:
        print("\nRow-level LLM")
        for k in [
            "rows_total",
            "ok_count",
            "ok_rate",
            "abstain_count",
            "missing_evidence_count",
            "avg_fair_value",
            "avg_confidence",
        ]:
            if k in llm:
                print(f"  {k}: {llm[k]}")

    comparison = health.get("signal_comparison", {})
    if comparison:
        print("\nSignal comparison")
        for k in [
            "rows_total",
            "avg_raw_abs_gap",
            "avg_normalized_abs_gap",
            "raw_direction_counts",
            "normalized_direction_counts",
            "group_mass_flag_counts",
        ]:
            if k in comparison:
                print(f"  {k}: {comparison[k]}")

    group_llm = health.get("llm_group_estimate", {})
    if group_llm:
        print("\nGroup-level LLM")
        for k in ["groups_total", "rows_total", "parse_status_counts", "normalization_applied_rows"]:
            if k in group_llm:
                print(f"  {k}: {group_llm[k]}")

    mode = health.get("estimate_mode_comparison", {})
    if mode:
        print("\nEstimate mode comparison")
        for k in [
            "rows_total",
            "groups_total",
            "group_prompt_rows_available",
            "raw_signal_count",
            "normalized_signal_count",
            "group_prompt_signal_count",
            "avg_raw_abs_gap",
            "avg_normalized_abs_gap",
            "avg_group_prompt_abs_gap",
        ]:
            if k in mode:
                print(f"  {k}: {mode[k]}")

    markout = health.get("markout", {})
    if markout:
        print("\nMarkouts")
        for k in [
            "rows_total",
            "status_counts",
            "status_by_horizon",
            "status_by_mode",
            "positive_directional_markout_rate",
            "avg_directional_markout_ready",
        ]:
            if k in markout:
                print(f"  {k}: {markout[k]}")

    taxonomy = health.get("error_taxonomy", {})
    if taxonomy:
        print("\nError taxonomy")
        for k in ["rows_total", "selected_direction_counts", "diagnostic_class_counts", "tag_counts"]:
            if k in taxonomy:
                print(f"  {k}: {taxonomy[k]}")

    calibration = health.get("calibration", {})
    if calibration:
        print("\nCalibration")
        for k in ["mode", "rows_scored", "summary_rows", "note"]:
            if k in calibration:
                print(f"  {k}: {calibration[k]}")


def validate_required_files(args: argparse.Namespace) -> None:
    required = []

    if not args.skip_orderbooks:
        required.append(args.markets_file)

    if args.skip_orderbooks:
        required.append(args.orderbook_file)

    if args.skip_signal_input:
        required.append(args.signal_input_file)

    if args.skip_row_llm:
        required.append(args.llm_estimates_file)

    if args.skip_signal_comparison:
        required.append(args.signal_comparison_file)

    missing = [p for p in required if not Path(p).exists()]
    if missing:
        print("Missing required files:")
        for p in missing:
            print(f"  - {p}")
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the full Reality Spread daily experiment pipeline.")

    # Core paths.
    p.add_argument("--markets-file", default="data/tracked_markets_final.csv")
    p.add_argument("--orderbook-file", default="data/orderbook_check.csv")
    p.add_argument("--signal-input-file", default="data/signal_inputs/signal_input_latest.csv")
    p.add_argument("--llm-estimates-file", default="data/llm_estimates/llm_estimates_latest.csv")
    p.add_argument("--signal-comparison-file", default="data/signals/signal_comparison_latest.csv")

    # Model settings.
    p.add_argument("--model", default=os.getenv("REALITY_SPREAD_LLM_MODEL", "qwen3:8b"))
    p.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))

    # Testing / speed controls.
    p.add_argument("--llm-limit", type=int, default=None, help="Limit row-level LLM rows.")
    p.add_argument("--group-limit", type=int, default=None, help="Limit group-level LLM groups.")
    p.add_argument("--mock-llm", action="store_true", help="Use mock row-level LLM estimates.")
    p.add_argument("--mock-group", action="store_true", help="Use mock group-level LLM estimates.")
    p.add_argument("--dry-run", action="store_true", help="Print commands without executing.")

    # Skip stages.
    p.add_argument("--skip-orderbooks", action="store_true")
    p.add_argument("--skip-signal-input", action="store_true")
    p.add_argument("--skip-row-llm", action="store_true")
    p.add_argument("--skip-signal-comparison", action="store_true")
    p.add_argument("--skip-group-llm", action="store_true")
    p.add_argument("--skip-mode-comparison", action="store_true")
    p.add_argument("--skip-markouts", action="store_true")
    p.add_argument("--skip-taxonomy", action="store_true")
    p.add_argument("--skip-calibration", action="store_true")

    # Markout/calibration settings.
    p.add_argument("--markout-horizons", nargs="+", default=["1d", "7d", "30d"])
    p.add_argument("--markout-tolerance-hours", type=float, default=12)
    p.add_argument("--signal-threshold", type=float, default=0.10)
    p.add_argument("--calibration-mode", choices=["soft", "resolution"], default="soft")

    # Manifest.
    p.add_argument("--runs-dir", default="data/runs")

    return p.parse_args()


def main() -> None:
    args = parse_args()

    validate_required_files(args)

    started = utc_now()
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    runs_dir = Path(args.runs_dir)
    ensure_dir(runs_dir)

    manifest_path = runs_dir / f"daily_run_{run_id}.json"
    latest_manifest_path = runs_dir / "latest_daily_run.json"

    manifest: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": iso(started),
        "ended_at": None,
        "duration_seconds": None,
        "status": "RUNNING",
        "model": args.model,
        "ollama_url": args.ollama_url,
        "args": vars(args),
        "steps": [],
        "health": {},
    }

    write_json(manifest_path, manifest)
    write_json(latest_manifest_path, manifest)

    py = sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = "."

    try:
        if not args.skip_orderbooks:
            run_command(
                "orderbook_snapshot",
                [
                    py,
                    "scripts/check_orderbooks.py",
                    "--input",
                    args.markets_file,
                    "--output",
                    args.orderbook_file,
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_signal_input:
            run_command(
                "build_signal_input",
                [
                    py,
                    "scripts/build_signal_input_snapshot.py",
                    "--input",
                    args.orderbook_file,
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_row_llm:
            cmd = [
                py,
                "scripts/run_llm_fair_values.py",
                "--input",
                args.signal_input_file,
                "--model",
                args.model,
                "--ollama-url",
                args.ollama_url,
            ]
            if args.llm_limit is not None:
                cmd.extend(["--limit", str(args.llm_limit)])
            if args.mock_llm:
                cmd.append("--mock")

            run_command(
                "row_level_llm_estimates",
                cmd,
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_signal_comparison:
            run_command(
                "build_signal_comparison",
                [
                    py,
                    "scripts/build_signal_comparison.py",
                    "--input",
                    args.llm_estimates_file,
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_group_llm:
            cmd = [
                py,
                "scripts/run_llm_group_fair_values.py",
                "--input",
                args.signal_input_file,
                "--model",
                args.model,
                "--ollama-url",
                args.ollama_url,
            ]
            if args.group_limit is not None:
                cmd.extend(["--limit-groups", str(args.group_limit)])
            if args.mock_group:
                cmd.append("--mock")

            run_command(
                "group_level_llm_estimates",
                cmd,
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_mode_comparison:
            run_command(
                "compare_estimate_modes",
                [
                    py,
                    "scripts/compare_estimate_modes.py",
                    "--signals",
                    args.signal_comparison_file,
                    "--group-estimates",
                    "data/llm_group_estimates/llm_group_estimates_latest.csv",
                    "--min-abs-gap",
                    str(args.signal_threshold),
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_markouts:
            run_command(
                "build_markouts",
                [
                    py,
                    "scripts/build_markouts.py",
                    "--signals",
                    "data/snapshots/signal_comparison_snapshots.csv",
                    "--prices",
                    "data/snapshots/signal_input_snapshots.csv",
                    "--horizons",
                    *args.markout_horizons,
                    "--tolerance-hours",
                    str(args.markout_tolerance_hours),
                    "--min-abs-gap",
                    str(args.signal_threshold),
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_taxonomy:
            run_command(
                "build_error_taxonomy",
                [
                    py,
                    "scripts/build_error_taxonomy.py",
                    "--signals",
                    args.signal_comparison_file,
                    "--markouts",
                    "data/markouts/markouts_latest.csv",
                    "--min-abs-gap",
                    str(args.signal_threshold),
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        if not args.skip_calibration:
            run_command(
                "build_calibration_scores",
                [
                    py,
                    "scripts/build_calibration_scores.py",
                    "--mode",
                    args.calibration_mode,
                    "--signals",
                    args.signal_comparison_file,
                    "--markouts",
                    "data/markouts/markouts_latest.csv",
                ],
                manifest=manifest,
                dry_run=args.dry_run,
                env=env,
            )

        manifest["status"] = "OK"

    finally:
        ended = utc_now()
        manifest["ended_at"] = iso(ended)
        manifest["duration_seconds"] = round((ended - started).total_seconds(), 3)
        manifest["health"] = summarize_health()
        write_json(manifest_path, manifest)
        write_json(latest_manifest_path, manifest)

        print_health_summary(manifest["health"])

        print("\n" + "=" * 100)
        print(f"Daily run manifest: {manifest_path}")
        print(f"Latest manifest:    {latest_manifest_path}")
        print(f"Final status:       {manifest['status']}")
        print(f"Duration seconds:   {manifest['duration_seconds']}")

        if manifest["status"] != "OK":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
