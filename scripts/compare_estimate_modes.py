#!/usr/bin/env python3
"""
scripts/compare_estimate_modes.py

Compares row-by-row raw estimates, row-normalized estimates, and optional group-prompt estimates.

Inputs:
    data/signals/signal_comparison_latest.csv
    data/llm_group_estimates/llm_group_estimates_latest.csv optional

Outputs:
    data/analysis/estimate_mode_comparison_latest.csv
    data/analysis/estimate_mode_group_summary_latest.csv
    data/health/latest_estimate_mode_comparison_health.json

Run:
    PYTHONPATH=. python scripts/compare_estimate_modes.py

Purpose:
    This is the "raw vs normalized vs group-prompt" diagnostic.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


ROW_COLUMNS = [
    "timestamp_utc",
    "tracking_id",
    "parent_market_name",
    "primary_outcome_to_track",
    "comparison_price",
    "row_raw_fair_value",
    "row_norm_fair_value",
    "group_prompt_fair_value",
    "raw_gap",
    "normalized_gap",
    "group_prompt_gap",
    "raw_abs_gap",
    "normalized_abs_gap",
    "group_prompt_abs_gap",
    "raw_direction",
    "normalized_direction",
    "group_prompt_direction",
    "raw_minus_norm",
    "group_prompt_minus_row_norm",
    "group_mass_flag",
    "use_normalized_for_analysis",
    "price_type",
    "liquidity_flags",
]

GROUP_COLUMNS = [
    "timestamp_utc",
    "parent_market_name",
    "group_n",
    "market_sum",
    "row_raw_sum",
    "row_norm_sum",
    "group_prompt_sum",
    "avg_raw_abs_gap",
    "avg_normalized_abs_gap",
    "avg_group_prompt_abs_gap",
    "raw_signal_count",
    "normalized_signal_count",
    "group_prompt_signal_count",
    "group_mass_flag",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v))
    except Exception:
        return None


def fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{v:.8f}".rstrip("0").rstrip(".")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], cols: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, latest_path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def direction(gap: Optional[float], min_abs_gap: float) -> str:
    if gap is None or abs(gap) < min_abs_gap:
        return "NO_SIGNAL"
    return "LLM_HIGHER" if gap > 0 else "MARKET_HIGHER"


def build_rows(
    signals: List[Dict[str, str]], group_estimates: List[Dict[str, str]], min_abs_gap: float, timestamp_utc: str
) -> List[Dict[str, str]]:
    group_by_tid = {r.get("tracking_id", ""): r for r in group_estimates if r.get("tracking_id", "")}
    out = []

    for sig in signals:
        tid = sig.get("tracking_id", "")
        gp = group_by_tid.get(tid, {})

        market = safe_float(sig.get("comparison_price"))
        raw = safe_float(sig.get("fair_value"))
        norm = safe_float(sig.get("fair_value_group_norm"))
        group_prompt = safe_float(gp.get("group_fair_value_norm"))

        raw_gap = raw - market if raw is not None and market is not None else None
        norm_gap = norm - market if norm is not None and market is not None else None
        gp_gap = group_prompt - market if group_prompt is not None and market is not None else None

        row = {
            "timestamp_utc": timestamp_utc,
            "tracking_id": tid,
            "parent_market_name": sig.get("parent_market_name", ""),
            "primary_outcome_to_track": sig.get("primary_outcome_to_track", ""),
            "comparison_price": fmt(market),
            "row_raw_fair_value": fmt(raw),
            "row_norm_fair_value": fmt(norm),
            "group_prompt_fair_value": fmt(group_prompt),
            "raw_gap": fmt(raw_gap),
            "normalized_gap": fmt(norm_gap),
            "group_prompt_gap": fmt(gp_gap),
            "raw_abs_gap": fmt(abs(raw_gap)) if raw_gap is not None else "",
            "normalized_abs_gap": fmt(abs(norm_gap)) if norm_gap is not None else "",
            "group_prompt_abs_gap": fmt(abs(gp_gap)) if gp_gap is not None else "",
            "raw_direction": direction(raw_gap, min_abs_gap),
            "normalized_direction": direction(norm_gap, min_abs_gap),
            "group_prompt_direction": direction(gp_gap, min_abs_gap),
            "raw_minus_norm": fmt(raw - norm) if raw is not None and norm is not None else "",
            "group_prompt_minus_row_norm": fmt(group_prompt - norm)
            if group_prompt is not None and norm is not None
            else "",
            "group_mass_flag": sig.get("group_mass_flag", ""),
            "use_normalized_for_analysis": sig.get("use_normalized_for_analysis", ""),
            "price_type": sig.get("price_type", ""),
            "liquidity_flags": sig.get("liquidity_flags", ""),
        }
        out.append(row)

    return out


def avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


def build_group_summary(rows: List[Dict[str, str]], timestamp_utc: str, min_abs_gap: float) -> List[Dict[str, str]]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        groups.setdefault(r["parent_market_name"], []).append(r)

    out = []
    for parent, items in sorted(groups.items()):
        market_sum = sum(safe_float(r.get("comparison_price")) or 0 for r in items)
        raw_sum = sum(safe_float(r.get("row_raw_fair_value")) or 0 for r in items)
        norm_sum = sum(safe_float(r.get("row_norm_fair_value")) or 0 for r in items)
        gp_vals = [safe_float(r.get("group_prompt_fair_value")) for r in items]
        gp_sum = sum(v for v in gp_vals if v is not None) if any(v is not None for v in gp_vals) else None

        out.append(
            {
                "timestamp_utc": timestamp_utc,
                "parent_market_name": parent,
                "group_n": str(len(items)),
                "market_sum": fmt(market_sum),
                "row_raw_sum": fmt(raw_sum),
                "row_norm_sum": fmt(norm_sum),
                "group_prompt_sum": fmt(gp_sum),
                "avg_raw_abs_gap": fmt(avg([safe_float(r.get("raw_abs_gap")) for r in items])),
                "avg_normalized_abs_gap": fmt(avg([safe_float(r.get("normalized_abs_gap")) for r in items])),
                "avg_group_prompt_abs_gap": fmt(avg([safe_float(r.get("group_prompt_abs_gap")) for r in items])),
                "raw_signal_count": str(sum(1 for r in items if r.get("raw_direction") != "NO_SIGNAL")),
                "normalized_signal_count": str(sum(1 for r in items if r.get("normalized_direction") != "NO_SIGNAL")),
                "group_prompt_signal_count": str(
                    sum(1 for r in items if r.get("group_prompt_direction") != "NO_SIGNAL")
                ),
                "group_mass_flag": "|".join(
                    sorted(set(r.get("group_mass_flag", "") for r in items if r.get("group_mass_flag", "")))
                ),
            }
        )

    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare estimate modes.")
    p.add_argument("--signals", default="data/signals/signal_comparison_latest.csv")
    p.add_argument("--group-estimates", default="data/llm_group_estimates/llm_group_estimates_latest.csv")
    p.add_argument("--row-output", default="data/analysis/estimate_mode_comparison_latest.csv")
    p.add_argument("--group-output", default="data/analysis/estimate_mode_group_summary_latest.csv")
    p.add_argument("--health-dir", default="data/health")
    p.add_argument("--min-abs-gap", type=float, default=0.10)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ts = utc_now()
    timestamp_utc = iso(ts)

    signals = read_csv(Path(args.signals))
    group_estimates = read_csv(Path(args.group_estimates))

    rows = build_rows(signals, group_estimates, args.min_abs_gap, timestamp_utc)
    summary = build_group_summary(rows, timestamp_utc, args.min_abs_gap)

    write_csv(Path(args.row_output), rows, ROW_COLUMNS)
    write_csv(Path(args.group_output), summary, GROUP_COLUMNS)

    health = {
        "timestamp_utc": timestamp_utc,
        "signals_input": args.signals,
        "group_estimates_input": args.group_estimates,
        "row_output": args.row_output,
        "group_output": args.group_output,
        "rows_total": len(rows),
        "groups_total": len(summary),
        "group_prompt_rows_available": sum(1 for r in rows if r.get("group_prompt_fair_value")),
        "raw_signal_count": sum(1 for r in rows if r.get("raw_direction") != "NO_SIGNAL"),
        "normalized_signal_count": sum(1 for r in rows if r.get("normalized_direction") != "NO_SIGNAL"),
        "group_prompt_signal_count": sum(1 for r in rows if r.get("group_prompt_direction") != "NO_SIGNAL"),
        "avg_raw_abs_gap": fmt(avg([safe_float(r.get("raw_abs_gap")) for r in rows])),
        "avg_normalized_abs_gap": fmt(avg([safe_float(r.get("normalized_abs_gap")) for r in rows])),
        "avg_group_prompt_abs_gap": fmt(avg([safe_float(r.get("group_prompt_abs_gap")) for r in rows])),
    }

    health_path = Path(args.health_dir) / f"estimate_mode_comparison_health_{ts.strftime('%Y%m%dT%H%M%SZ')}.json"
    latest_health = Path(args.health_dir) / "latest_estimate_mode_comparison_health.json"
    write_json(health_path, latest_health, health)

    print(f"Rows: {len(rows)}")
    print(f"Groups: {len(summary)}")
    print(f"Group prompt rows available: {health['group_prompt_rows_available']}")
    print(f"Raw signals: {health['raw_signal_count']}")
    print(f"Normalized signals: {health['normalized_signal_count']}")
    print(f"Group-prompt signals: {health['group_prompt_signal_count']}")
    print(f"Wrote row comparison: {args.row_output}")
    print(f"Wrote group summary: {args.group_output}")


if __name__ == "__main__":
    main()
