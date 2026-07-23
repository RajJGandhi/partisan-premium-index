#!/usr/bin/env python3
"""
scripts/build_signal_comparison.py

Builds the Reality Spread comparison layer:

    LLM fair_value vs market comparison_price

Input:
    data/llm_estimates/llm_estimates_latest.csv

Outputs:
    data/signals/signal_comparison_latest.csv
    data/signals/signal_comparison_<run_id>.csv
    data/snapshots/signal_comparison_snapshots.csv
    data/health/latest_signal_comparison_health.json
    data/health/signal_comparison_health_<run_id>.json

Why this script exists:
- The LLM fair-value runner estimates each option row independently.
- Many parent markets are mutually exclusive option sets.
- Independent row estimates can fail probability-mass coherence, e.g.
      House seat ranges sum to 4.00 instead of ~1.00.
- This script preserves raw gaps, but also computes group-normalized LLM values
  for multi-option parent groups.

Core fields:
    raw_gap = fair_value - comparison_price
    normalized_gap = fair_value_group_norm - comparison_price
    signal_direction
    signal_strength
    group_fair_sum
    group_market_sum
    group_mass_flag

Run:
    PYTHONPATH=. python scripts/build_signal_comparison.py \
      --input data/llm_estimates/llm_estimates_latest.csv

Optional:
    PYTHONPATH=. python scripts/build_signal_comparison.py \
      --input data/llm_estimates/llm_estimates_latest.csv \
      --min-abs-gap 0.10
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


OUTPUT_COLUMNS = [
    "signal_id",
    "run_id",
    "timestamp_utc",
    "prompt_version",
    "model_backend",
    "model_name",
    "tracking_id",
    "parent_market_name",
    "market_name",
    "primary_outcome_to_track",
    "region",
    "bucket",
    "system_type",
    "underlying_event_group",
    "gamma_market_id",
    "condition_id",
    "exact_polymarket_slug",
    "market_url",
    "token_id",
    "outcome_contract_question",
    "fair_value",
    "fair_value_group_norm",
    "confidence",
    "should_abstain",
    "comparison_price",
    "price_type",
    "liquidity_flags",
    "raw_gap",
    "raw_abs_gap",
    "normalized_gap",
    "normalized_abs_gap",
    "signal_direction_raw",
    "signal_direction_normalized",
    "signal_strength_raw",
    "signal_strength_normalized",
    "group_n",
    "group_fair_sum",
    "group_market_sum",
    "group_mass_flag",
    "use_normalized_for_analysis",
    "analysis_price_basis",
    "rationale_short",
    "key_uncertainties_json",
    "base_rate_notes",
    "parse_status",
    "signal_ready",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts: Optional[dt.datetime] = None) -> str:
    return (ts or utc_now()).isoformat().replace("+00:00", "Z")


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except Exception:
        return None


def fmt(value: Optional[float], decimals: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: Sequence[str], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def write_health(path: Path, latest_path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def direction(gap: Optional[float], min_abs_gap: float) -> str:
    if gap is None:
        return ""
    if abs(gap) < min_abs_gap:
        return "NO_SIGNAL"
    return "LLM_HIGHER" if gap > 0 else "MARKET_HIGHER"


def strength(abs_gap: Optional[float]) -> str:
    if abs_gap is None:
        return ""
    if abs_gap >= 0.30:
        return "extreme"
    if abs_gap >= 0.20:
        return "strong"
    if abs_gap >= 0.10:
        return "medium"
    if abs_gap >= 0.05:
        return "weak"
    return "none"


def group_key(row: Dict[str, str]) -> str:
    # Parent market is the cleanest grouping key for this v0.1 universe.
    return row.get("parent_market_name", "")


def group_mass_flag(group_n: int, fair_sum: float, market_sum: float) -> str:
    """
    Flags probability-mass issues for mutually exclusive groups.

    For binary groups, fair_sum should usually be close to 1.
    For multi-option exhaustive groups, fair_sum should also often be close to 1.
    However, not every parent group is guaranteed exhaustive, so this is a warning,
    not a hard failure.
    """
    flags: List[str] = []

    if group_n >= 2:
        if fair_sum > 1.25:
            flags.append("llm_mass_too_high")
        elif fair_sum < 0.75:
            flags.append("llm_mass_too_low")

        if market_sum > 1.20:
            flags.append("market_mass_high")
        elif market_sum < 0.80:
            flags.append("market_mass_low")

    return "|".join(flags)


def should_normalize(group_n: int, fair_sum: float, market_sum: float) -> bool:
    """
    Use normalized LLM values when:
    - group has more than 2 options, or
    - group has explicit mass issue.

    For clean binary groups, raw fair values may be more interpretable.
    """
    if group_n > 2:
        return True
    if fair_sum > 1.25 or fair_sum < 0.75:
        return True
    return False


def build_group_stats(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(group_key(row), []).append(row)

    stats: Dict[str, Dict[str, Any]] = {}
    for key, items in groups.items():
        fair_values = [safe_float(r.get("fair_value")) for r in items]
        market_values = [safe_float(r.get("comparison_price")) for r in items]

        fair_values_clean = [x for x in fair_values if x is not None]
        market_values_clean = [x for x in market_values if x is not None]

        fair_sum = sum(fair_values_clean)
        market_sum = sum(market_values_clean)
        n = len(items)

        stats[key] = {
            "n": n,
            "fair_sum": fair_sum,
            "market_sum": market_sum,
            "flag": group_mass_flag(n, fair_sum, market_sum),
            "normalize": should_normalize(n, fair_sum, market_sum),
        }
    return stats


def convert_row(row: Dict[str, str], stats: Dict[str, Dict[str, Any]], min_abs_gap: float) -> Dict[str, str]:
    key = group_key(row)
    gs = stats[key]

    fair = safe_float(row.get("fair_value"))
    market = safe_float(row.get("comparison_price"))

    fair_norm: Optional[float] = None
    if fair is not None and gs["fair_sum"] > 0:
        fair_norm = fair / gs["fair_sum"]

    raw_gap: Optional[float] = None
    raw_abs_gap: Optional[float] = None
    if fair is not None and market is not None:
        raw_gap = fair - market
        raw_abs_gap = abs(raw_gap)

    norm_gap: Optional[float] = None
    norm_abs_gap: Optional[float] = None
    if fair_norm is not None and market is not None:
        norm_gap = fair_norm - market
        norm_abs_gap = abs(norm_gap)

    use_norm = bool(gs["normalize"])
    analysis_basis = "normalized_gap" if use_norm else "raw_gap"

    out = {col: "" for col in OUTPUT_COLUMNS}
    out.update(
        {
            "signal_id": f"{row.get('run_id', '')}:{row.get('tracking_id', '')}",
            "run_id": row.get("run_id", ""),
            "timestamp_utc": row.get("timestamp_utc", ""),
            "prompt_version": row.get("prompt_version", ""),
            "model_backend": row.get("model_backend", ""),
            "model_name": row.get("model_name", ""),
            "tracking_id": row.get("tracking_id", ""),
            "parent_market_name": row.get("parent_market_name", ""),
            "market_name": row.get("market_name", ""),
            "primary_outcome_to_track": row.get("primary_outcome_to_track", ""),
            "region": row.get("region", ""),
            "bucket": row.get("bucket", ""),
            "system_type": row.get("system_type", ""),
            "underlying_event_group": row.get("underlying_event_group", ""),
            "gamma_market_id": row.get("gamma_market_id", ""),
            "condition_id": row.get("condition_id", ""),
            "exact_polymarket_slug": row.get("exact_polymarket_slug", ""),
            "market_url": row.get("market_url", ""),
            "token_id": row.get("token_id", ""),
            "outcome_contract_question": row.get("outcome_contract_question", ""),
            "fair_value": row.get("fair_value", ""),
            "fair_value_group_norm": fmt(fair_norm),
            "confidence": row.get("confidence", ""),
            "should_abstain": row.get("should_abstain", ""),
            "comparison_price": row.get("comparison_price", ""),
            "price_type": row.get("price_type", ""),
            "liquidity_flags": row.get("liquidity_flags", ""),
            "raw_gap": fmt(raw_gap),
            "raw_abs_gap": fmt(raw_abs_gap),
            "normalized_gap": fmt(norm_gap),
            "normalized_abs_gap": fmt(norm_abs_gap),
            "signal_direction_raw": direction(raw_gap, min_abs_gap),
            "signal_direction_normalized": direction(norm_gap, min_abs_gap),
            "signal_strength_raw": strength(raw_abs_gap),
            "signal_strength_normalized": strength(norm_abs_gap),
            "group_n": str(gs["n"]),
            "group_fair_sum": fmt(gs["fair_sum"]),
            "group_market_sum": fmt(gs["market_sum"]),
            "group_mass_flag": gs["flag"],
            "use_normalized_for_analysis": str(use_norm).lower(),
            "analysis_price_basis": analysis_basis,
            "rationale_short": row.get("rationale_short", ""),
            "key_uncertainties_json": row.get("key_uncertainties_json", ""),
            "base_rate_notes": row.get("base_rate_notes", ""),
            "parse_status": row.get("parse_status", ""),
            "signal_ready": row.get("signal_ready", ""),
        }
    )
    return out


def build_health(
    rows: List[Dict[str, str]],
    input_path: str,
    latest_output: str,
    snapshot_output: str,
    append_output: str,
    min_abs_gap: float,
) -> Dict[str, Any]:
    def count_by(col: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for row in rows:
            key = row.get(col, "")
            out[key] = out.get(key, 0) + 1
        return out

    raw_gaps = [safe_float(r.get("raw_abs_gap")) for r in rows]
    raw_gaps = [x for x in raw_gaps if x is not None]

    norm_gaps = [safe_float(r.get("normalized_abs_gap")) for r in rows]
    norm_gaps = [x for x in norm_gaps if x is not None]

    flagged_groups = sorted(
        {
            r["parent_market_name"]: {
                "group_n": r["group_n"],
                "group_fair_sum": r["group_fair_sum"],
                "group_market_sum": r["group_market_sum"],
                "group_mass_flag": r["group_mass_flag"],
                "use_normalized_for_analysis": r["use_normalized_for_analysis"],
            }
            for r in rows
            if r.get("group_mass_flag")
        }.items(),
        key=lambda kv: kv[0],
    )

    top_raw = sorted(rows, key=lambda r: safe_float(r.get("raw_abs_gap")) or -1, reverse=True)[:25]
    top_norm = sorted(rows, key=lambda r: safe_float(r.get("normalized_abs_gap")) or -1, reverse=True)[:25]

    return {
        "run_id": rows[0]["run_id"] if rows else "",
        "timestamp_utc": rows[0]["timestamp_utc"] if rows else iso_utc(),
        "input_path": input_path,
        "latest_output": latest_output,
        "snapshot_output": snapshot_output,
        "append_output": append_output,
        "rows_total": len(rows),
        "min_abs_gap": min_abs_gap,
        "raw_direction_counts": count_by("signal_direction_raw"),
        "normalized_direction_counts": count_by("signal_direction_normalized"),
        "raw_strength_counts": count_by("signal_strength_raw"),
        "normalized_strength_counts": count_by("signal_strength_normalized"),
        "group_mass_flag_counts": count_by("group_mass_flag"),
        "use_normalized_counts": count_by("use_normalized_for_analysis"),
        "avg_raw_abs_gap": round(sum(raw_gaps) / len(raw_gaps), 6) if raw_gaps else None,
        "max_raw_abs_gap": round(max(raw_gaps), 6) if raw_gaps else None,
        "avg_normalized_abs_gap": round(sum(norm_gaps) / len(norm_gaps), 6) if norm_gaps else None,
        "max_normalized_abs_gap": round(max(norm_gaps), 6) if norm_gaps else None,
        "flagged_groups": flagged_groups,
        "top_raw_abs_gaps": [
            {
                "tracking_id": r["tracking_id"],
                "parent_market_name": r["parent_market_name"],
                "outcome": r["primary_outcome_to_track"],
                "fair_value": r["fair_value"],
                "comparison_price": r["comparison_price"],
                "raw_gap": r["raw_gap"],
                "raw_abs_gap": r["raw_abs_gap"],
                "group_mass_flag": r["group_mass_flag"],
            }
            for r in top_raw
        ],
        "top_normalized_abs_gaps": [
            {
                "tracking_id": r["tracking_id"],
                "parent_market_name": r["parent_market_name"],
                "outcome": r["primary_outcome_to_track"],
                "fair_value_group_norm": r["fair_value_group_norm"],
                "comparison_price": r["comparison_price"],
                "normalized_gap": r["normalized_gap"],
                "normalized_abs_gap": r["normalized_abs_gap"],
                "group_mass_flag": r["group_mass_flag"],
            }
            for r in top_norm
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Reality Spread signal comparison output.")
    parser.add_argument("--input", default="data/llm_estimates/llm_estimates_latest.csv")
    parser.add_argument("--latest-output", default="data/signals/signal_comparison_latest.csv")
    parser.add_argument("--snapshot-dir", default="data/signals")
    parser.add_argument("--append-output", default="data/snapshots/signal_comparison_snapshots.csv")
    parser.add_argument("--health-dir", default="data/health")
    parser.add_argument("--min-abs-gap", type=float, default=0.10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_rows = read_csv(Path(args.input))

    # Keep only usable rows.
    usable = [
        r
        for r in source_rows
        if r.get("parse_status") == "OK"
        and r.get("signal_ready", "").lower() == "true"
        and safe_float(r.get("fair_value")) is not None
        and safe_float(r.get("comparison_price")) is not None
    ]

    stats = build_group_stats(usable)
    converted = [convert_row(r, stats, args.min_abs_gap) for r in usable]

    run_id = converted[0]["run_id"] if converted else utc_now().strftime("%Y%m%dT%H%M%SZ")

    latest_output = Path(args.latest_output)
    snapshot_output = Path(args.snapshot_dir) / f"signal_comparison_{run_id}.csv"
    append_output = Path(args.append_output)

    write_csv(latest_output, converted, OUTPUT_COLUMNS, append=False)
    write_csv(snapshot_output, converted, OUTPUT_COLUMNS, append=False)
    write_csv(append_output, converted, OUTPUT_COLUMNS, append=True)

    health = build_health(
        converted,
        input_path=args.input,
        latest_output=str(latest_output),
        snapshot_output=str(snapshot_output),
        append_output=str(append_output),
        min_abs_gap=args.min_abs_gap,
    )

    health_path = Path(args.health_dir) / f"signal_comparison_health_{run_id}.json"
    latest_health = Path(args.health_dir) / "latest_signal_comparison_health.json"
    write_health(health_path, latest_health, health)

    print(f"Rows total: {health['rows_total']}")
    print(f"Avg raw abs gap: {health['avg_raw_abs_gap']}")
    print(f"Avg normalized abs gap: {health['avg_normalized_abs_gap']}")
    print(f"Max raw abs gap: {health['max_raw_abs_gap']}")
    print(f"Max normalized abs gap: {health['max_normalized_abs_gap']}")

    print("\nRaw signal directions:")
    for k, v in sorted(health["raw_direction_counts"].items()):
        print(f"  {k}: {v}")

    print("\nNormalized signal directions:")
    for k, v in sorted(health["normalized_direction_counts"].items()):
        print(f"  {k}: {v}")

    print("\nGroup mass flags:")
    for k, v in sorted(health["group_mass_flag_counts"].items()):
        label = k or "clean"
        print(f"  {label}: {v}")

    print("\nWrote:")
    print(f"  Latest signals: {latest_output}")
    print(f"  Timestamped signals: {snapshot_output}")
    print(f"  Append-only signals: {append_output}")
    print(f"  Latest health: {latest_health}")
    print(f"  Timestamped health: {health_path}")

    if not converted:
        raise SystemExit("No usable signal rows generated.")


if __name__ == "__main__":
    main()
