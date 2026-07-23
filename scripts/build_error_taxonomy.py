#!/usr/bin/env python3
"""
scripts/build_error_taxonomy.py

PATCH v0.2.2

Fixes selected_direction blank by inferring direction directly from:
    raw_gap
    normalized_gap

Default signal threshold:
    abs(gap) >= 0.10
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
    "fair_value",
    "fair_value_group_norm",
    "raw_gap",
    "normalized_gap",
    "selected_gap",
    "selected_abs_gap",
    "selected_direction",
    "selected_strength",
    "error_tags",
    "diagnostic_class",
    "analysis_action",
    "group_n",
    "group_fair_sum",
    "group_market_sum",
    "group_mass_flag",
    "use_normalized_for_analysis",
    "price_type",
    "liquidity_flags",
    "markout_status",
    "directional_markout",
]

SUMMARY_COLUMNS = [
    "timestamp_utc",
    "diagnostic_class",
    "count",
    "avg_selected_abs_gap",
    "positive_markout_rate",
    "avg_directional_markout",
    "recommended_action",
]


def first_nonempty(row: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key, "")
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


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


def infer_direction(gap: Optional[float], min_abs_gap: float) -> str:
    if gap is None or abs(gap) < min_abs_gap:
        return "NO_SIGNAL"
    return "LLM_HIGHER" if gap > 0 else "MARKET_HIGHER"


def infer_strength(abs_gap: Optional[float]) -> str:
    if abs_gap is None:
        return ""
    if abs_gap >= 0.25:
        return "large"
    if abs_gap >= 0.15:
        return "medium"
    if abs_gap >= 0.10:
        return "small"
    return "none"


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


def latest_markout_by_tid(markouts: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    out = {}
    priority = {"READY": 0, "PENDING": 1}
    horizon_priority = {"1d": 0, "7d": 1, "30d": 2}
    for m in markouts:
        tid = m.get("tracking_id", "")
        if not tid:
            continue
        key = (priority.get(m.get("markout_status", ""), 9), horizon_priority.get(m.get("horizon", ""), 9))
        prev = out.get(tid)
        if prev is None:
            out[tid] = m
        else:
            prev_key = (
                priority.get(prev.get("markout_status", ""), 9),
                horizon_priority.get(prev.get("horizon", ""), 9),
            )
            if key < prev_key:
                out[tid] = m
    return out


def selected_signal_fields(row: Dict[str, str], min_abs_gap: float) -> Dict[str, Any]:
    use_norm = row.get("use_normalized_for_analysis", "").lower() == "true"

    if use_norm:
        gap = safe_float(first_nonempty(row, "normalized_gap"))
        abs_gap = safe_float(first_nonempty(row, "normalized_abs_gap"))
        if abs_gap is None and gap is not None:
            abs_gap = abs(gap)

        direction = first_nonempty(row, "normalized_signal_direction", "normalized_direction")
        if not direction:
            direction = infer_direction(gap, min_abs_gap)

        strength = first_nonempty(row, "normalized_signal_strength", "normalized_strength")
        if not strength:
            strength = infer_strength(abs_gap)

        return {
            "selected_gap": gap,
            "selected_abs_gap": abs_gap,
            "selected_direction": direction,
            "selected_strength": strength,
        }

    gap = safe_float(first_nonempty(row, "raw_gap"))
    abs_gap = safe_float(first_nonempty(row, "raw_abs_gap"))
    if abs_gap is None and gap is not None:
        abs_gap = abs(gap)

    direction = first_nonempty(row, "raw_signal_direction", "raw_direction")
    if not direction:
        direction = infer_direction(gap, min_abs_gap)

    strength = first_nonempty(row, "raw_signal_strength", "raw_strength")
    if not strength:
        strength = infer_strength(abs_gap)

    return {
        "selected_gap": gap,
        "selected_abs_gap": abs_gap,
        "selected_direction": direction,
        "selected_strength": strength,
    }


def classify(row: Dict[str, str], min_abs_gap: float) -> Dict[str, str]:
    tags: List[str] = []
    selected = selected_signal_fields(row, min_abs_gap)

    group_mass_flag = row.get("group_mass_flag", "")
    price_type = row.get("price_type", "")
    liquidity_flags = row.get("liquidity_flags", "")
    parent = row.get("parent_market_name", "").lower()

    try:
        group_n = int(float(row.get("group_n", "1") or 1))
    except Exception:
        group_n = 1

    market_sum = safe_float(row.get("group_market_sum"))

    if "llm_mass_too_high" in group_mass_flag:
        tags.append("probability_mass_incoherence")
    if "market_mass_low" in group_mass_flag or (market_sum is not None and group_n > 2 and market_sum < 0.8):
        tags.append("incomplete_market_group")
    if price_type == "ask_only":
        tags.append("ask_only_price")
    if "thin" in liquidity_flags:
        tags.append("thin_liquidity")
    if "wide_spread" in liquidity_flags:
        tags.append("wide_spread")
    if group_n <= 2 and not group_mass_flag:
        tags.append("binary_clean")
    if group_n > 2:
        tags.append("multi_option_market")
    if any(x in parent for x in ["seat", "range", "margin"]):
        tags.append("range_or_bucket_market")

    selected_abs_gap = selected["selected_abs_gap"]
    selected_dir = selected["selected_direction"]

    if selected_abs_gap is not None and selected_abs_gap >= 0.25:
        tags.append("large_gap")
    if selected_dir == "NO_SIGNAL" or selected_abs_gap is None or selected_abs_gap < min_abs_gap:
        tags.append("no_material_signal")

    markout_status = row.get("markout_status", "")
    markout = safe_float(row.get("directional_markout"))
    if markout_status == "READY":
        tags.append("markout_ready")
        if markout is not None and markout > 0:
            tags.append("markout_supported")
        elif markout is not None and markout < 0:
            tags.append("markout_not_supported")
        else:
            tags.append("markout_flat")
    elif markout_status:
        tags.append("markout_pending")

    if "incomplete_market_group" in tags:
        diagnostic = "diagnostic_only_incomplete_market"
        action = "Exclude from headline signal analysis until group is completed."
    elif "probability_mass_incoherence" in tags and "multi_option_market" in tags:
        diagnostic = "use_group_prompt_or_normalized_only"
        action = "Do not use raw row-by-row gap; use normalized/group-prompt estimate and log coherence failure."
    elif "binary_clean" in tags and "large_gap" in tags:
        diagnostic = "clean_binary_disagreement"
        action = "Track as high-priority disagreement candidate."
    elif "ask_only_price" in tags or "thin_liquidity" in tags or "wide_spread" in tags:
        diagnostic = "liquidity_caution"
        action = "Keep but downweight; verify order book depth before interpreting."
    elif "no_material_signal" in tags:
        diagnostic = "no_signal"
        action = "Retain for calibration/base rate, not active disagreement."
    elif "multi_option_market" in tags:
        diagnostic = "multi_option_interpretable_with_normalization"
        action = "Use normalized or group-prompt estimate; compare against markouts."
    else:
        diagnostic = "standard_signal"
        action = "Track normally."

    return {
        "selected_gap": fmt(selected["selected_gap"]),
        "selected_abs_gap": fmt(selected_abs_gap),
        "selected_direction": selected_dir,
        "selected_strength": selected["selected_strength"],
        "error_tags": "|".join(tags),
        "diagnostic_class": diagnostic,
        "analysis_action": action,
    }


def build_rows(
    signals: List[Dict[str, str]], markouts: List[Dict[str, str]], timestamp_utc: str, min_abs_gap: float
) -> List[Dict[str, str]]:
    mout = latest_markout_by_tid(markouts)
    rows = []
    for s in signals:
        m = mout.get(s.get("tracking_id", ""), {})
        combined = {**s, **m}
        c = classify(combined, min_abs_gap=min_abs_gap)

        out = {col: "" for col in ROW_COLUMNS}
        out.update(
            {
                "timestamp_utc": timestamp_utc,
                "tracking_id": s.get("tracking_id", ""),
                "parent_market_name": s.get("parent_market_name", ""),
                "primary_outcome_to_track": s.get("primary_outcome_to_track", ""),
                "comparison_price": s.get("comparison_price", ""),
                "fair_value": s.get("fair_value", ""),
                "fair_value_group_norm": s.get("fair_value_group_norm", ""),
                "raw_gap": s.get("raw_gap", ""),
                "normalized_gap": s.get("normalized_gap", ""),
                "group_n": s.get("group_n", ""),
                "group_fair_sum": s.get("group_fair_sum", ""),
                "group_market_sum": s.get("group_market_sum", ""),
                "group_mass_flag": s.get("group_mass_flag", ""),
                "use_normalized_for_analysis": s.get("use_normalized_for_analysis", ""),
                "price_type": s.get("price_type", ""),
                "liquidity_flags": s.get("liquidity_flags", ""),
                "markout_status": m.get("markout_status", ""),
                "directional_markout": m.get("directional_markout", ""),
            }
        )
        out.update(c)
        rows.append(out)
    return rows


def avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


def build_summary(rows: List[Dict[str, str]], timestamp_utc: str) -> List[Dict[str, str]]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        groups.setdefault(r["diagnostic_class"], []).append(r)

    recommendations = {
        "diagnostic_only_incomplete_market": "Exclude from headline analysis.",
        "use_group_prompt_or_normalized_only": "Use normalized/group-prompt only.",
        "clean_binary_disagreement": "Track as priority candidate.",
        "liquidity_caution": "Downweight and monitor book depth.",
        "no_signal": "Retain for calibration only.",
        "multi_option_interpretable_with_normalization": "Use normalized/group-prompt estimate.",
        "standard_signal": "Track normally.",
    }

    out = []
    for cls, items in sorted(groups.items()):
        mark_clean = [
            safe_float(r.get("directional_markout"))
            for r in items
            if r.get("markout_status") == "READY" and safe_float(r.get("directional_markout")) is not None
        ]
        pos_rate = sum(1 for x in mark_clean if x > 0) / len(mark_clean) if mark_clean else None

        out.append(
            {
                "timestamp_utc": timestamp_utc,
                "diagnostic_class": cls,
                "count": str(len(items)),
                "avg_selected_abs_gap": fmt(avg([safe_float(r.get("selected_abs_gap")) for r in items])),
                "positive_markout_rate": fmt(pos_rate),
                "avg_directional_markout": fmt(avg(mark_clean)),
                "recommended_action": recommendations.get(cls, ""),
            }
        )
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build error taxonomy report.")
    p.add_argument("--signals", default="data/signals/signal_comparison_latest.csv")
    p.add_argument("--markouts", default="data/markouts/markouts_latest.csv")
    p.add_argument("--row-output", default="data/analysis/error_taxonomy_latest.csv")
    p.add_argument("--summary-output", default="data/analysis/error_taxonomy_summary_latest.csv")
    p.add_argument("--health-dir", default="data/health")
    p.add_argument("--min-abs-gap", type=float, default=0.10)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ts = utc_now()
    timestamp_utc = iso(ts)

    signals = read_csv(Path(args.signals))
    markouts = read_csv(Path(args.markouts))

    rows = build_rows(signals, markouts, timestamp_utc, args.min_abs_gap)
    summary = build_summary(rows, timestamp_utc)

    write_csv(Path(args.row_output), rows, ROW_COLUMNS)
    write_csv(Path(args.summary_output), summary, SUMMARY_COLUMNS)

    class_counts: Dict[str, int] = {}
    tag_counts: Dict[str, int] = {}
    selected_direction_counts: Dict[str, int] = {}

    for r in rows:
        class_counts[r["diagnostic_class"]] = class_counts.get(r["diagnostic_class"], 0) + 1
        selected_direction_counts[r.get("selected_direction", "")] = (
            selected_direction_counts.get(r.get("selected_direction", ""), 0) + 1
        )
        for tag in r["error_tags"].split("|"):
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    health = {
        "timestamp_utc": timestamp_utc,
        "signals_input": args.signals,
        "markouts_input": args.markouts,
        "row_output": args.row_output,
        "summary_output": args.summary_output,
        "min_abs_gap": args.min_abs_gap,
        "rows_total": len(rows),
        "diagnostic_class_counts": class_counts,
        "selected_direction_counts": selected_direction_counts,
        "tag_counts": tag_counts,
        "summary": summary,
    }

    health_path = Path(args.health_dir) / f"error_taxonomy_health_{ts.strftime('%Y%m%dT%H%M%SZ')}.json"
    latest_health = Path(args.health_dir) / "latest_error_taxonomy_health.json"
    write_json(health_path, latest_health, health)

    print(f"Rows: {len(rows)}")
    print("Selected directions:")
    for k, v in sorted(selected_direction_counts.items()):
        print(f"  {k or '(blank)'}: {v}")
    print("Diagnostic classes:")
    for k, v in sorted(class_counts.items()):
        print(f"  {k}: {v}")
    print(f"Wrote taxonomy: {args.row_output}")
    print(f"Wrote summary: {args.summary_output}")


if __name__ == "__main__":
    main()
