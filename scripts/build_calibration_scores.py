#!/usr/bin/env python3
"""
scripts/build_calibration_scores.py

Builds calibration/Brier/log-loss scoring once markets resolve OR using future prices as a soft markout target.

Modes:
  1. resolution mode:
      Requires data/resolutions.csv with columns:
        tracking_id,resolved_outcome,resolved_at
      resolved_outcome must be 0 or 1.

  2. soft mode:
      Uses markouts file future_price as a soft target.
      This is not true calibration to event outcomes, but it is useful for interim forward-test analysis.

Inputs:
    data/signals/signal_comparison_latest.csv
    data/markouts/markouts_latest.csv
    data/resolutions.csv optional

Outputs:
    data/scoring/calibration_scores_latest.csv
    data/scoring/calibration_summary_latest.csv
    data/health/latest_calibration_health.json

Run:
    # Soft scoring from markouts:
    PYTHONPATH=. python scripts/build_calibration_scores.py --mode soft

    # Resolution scoring:
    PYTHONPATH=. python scripts/build_calibration_scores.py --mode resolution --resolutions data/resolutions.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCORE_COLUMNS = [
    "score_id",
    "timestamp_utc",
    "mode",
    "horizon",
    "tracking_id",
    "parent_market_name",
    "primary_outcome_to_track",
    "target_value",
    "target_type",
    "market_prob",
    "llm_raw_prob",
    "llm_norm_prob",
    "llm_selected_prob",
    "selected_prob_source",
    "market_brier",
    "llm_raw_brier",
    "llm_norm_brier",
    "llm_selected_brier",
    "market_log_loss",
    "llm_raw_log_loss",
    "llm_norm_log_loss",
    "llm_selected_log_loss",
    "llm_selected_brier_skill_vs_market",
    "group_mass_flag",
    "use_normalized_for_analysis",
]

SUMMARY_COLUMNS = [
    "timestamp_utc",
    "mode",
    "horizon",
    "prob_source",
    "n",
    "avg_brier",
    "avg_log_loss",
    "avg_brier_skill_vs_market",
    "avg_market_brier",
    "calibration_bin",
    "bin_n",
    "bin_avg_pred",
    "bin_avg_target",
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


def clip_prob(p: Optional[float], eps: float = 1e-6) -> Optional[float]:
    if p is None:
        return None
    return min(1 - eps, max(eps, p))


def brier(p: Optional[float], y: Optional[float]) -> Optional[float]:
    if p is None or y is None:
        return None
    return (p - y) ** 2


def log_loss(p: Optional[float], y: Optional[float]) -> Optional[float]:
    if p is None or y is None:
        return None
    p = clip_prob(p)
    if p is None:
        return None
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def fmt(x: Optional[float]) -> str:
    if x is None:
        return ""
    return f"{x:.8f}".rstrip("0").rstrip(".")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, latest_path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def index_by_tid(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    return {r.get("tracking_id", ""): r for r in rows if r.get("tracking_id", "")}


def load_resolution_targets(path: Path) -> Dict[str, Dict[str, str]]:
    rows = read_csv(path)
    out = {}
    for r in rows:
        tid = r.get("tracking_id", "")
        y = safe_float(r.get("resolved_outcome", ""))
        if tid and y in {0.0, 1.0}:
            out[tid] = r
    return out


def selected_llm_prob(signal: Dict[str, str]) -> Tuple[Optional[float], str]:
    use_norm = signal.get("use_normalized_for_analysis", "").lower() == "true"
    if use_norm:
        p = safe_float(signal.get("fair_value_group_norm", ""))
        if p is not None:
            return p, "normalized"
    p = safe_float(signal.get("fair_value", ""))
    return p, "raw"


def build_resolution_scores(
    signals: List[Dict[str, str]], resolutions: Dict[str, Dict[str, str]], timestamp_utc: str
) -> List[Dict[str, str]]:
    rows = []
    for sig in signals:
        tid = sig.get("tracking_id", "")
        res = resolutions.get(tid)
        if not res:
            continue
        target = safe_float(res.get("resolved_outcome"))
        if target not in {0.0, 1.0}:
            continue
        rows.append(score_one(sig, target, "resolution", "final", timestamp_utc, mode="resolution"))
    return rows


def build_soft_scores(
    signals: List[Dict[str, str]], markouts: List[Dict[str, str]], timestamp_utc: str
) -> List[Dict[str, str]]:
    sig_idx = index_by_tid(signals)
    rows = []
    for m in markouts:
        if m.get("markout_status") != "READY":
            continue
        tid = m.get("tracking_id", "")
        sig = sig_idx.get(tid)
        if not sig:
            continue
        target = safe_float(m.get("future_price"))
        if target is None:
            continue
        rows.append(score_one(sig, target, "future_price", m.get("horizon", ""), timestamp_utc, mode="soft"))
    return rows


def score_one(
    sig: Dict[str, str], target: float, target_type: str, horizon: str, timestamp_utc: str, mode: str
) -> Dict[str, str]:
    market_p = safe_float(sig.get("comparison_price"))
    llm_raw = safe_float(sig.get("fair_value"))
    llm_norm = safe_float(sig.get("fair_value_group_norm"))
    llm_sel, sel_source = selected_llm_prob(sig)

    market_b = brier(market_p, target)
    raw_b = brier(llm_raw, target)
    norm_b = brier(llm_norm, target)
    sel_b = brier(llm_sel, target)

    market_l = log_loss(market_p, target)
    raw_l = log_loss(llm_raw, target)
    norm_l = log_loss(llm_norm, target)
    sel_l = log_loss(llm_sel, target)

    skill = None
    if market_b is not None and sel_b is not None and market_b > 0:
        skill = 1 - (sel_b / market_b)

    return {
        "score_id": f"{mode}:{horizon}:{sig.get('run_id', '')}:{sig.get('tracking_id', '')}",
        "timestamp_utc": timestamp_utc,
        "mode": mode,
        "horizon": horizon,
        "tracking_id": sig.get("tracking_id", ""),
        "parent_market_name": sig.get("parent_market_name", ""),
        "primary_outcome_to_track": sig.get("primary_outcome_to_track", ""),
        "target_value": fmt(target),
        "target_type": target_type,
        "market_prob": fmt(market_p),
        "llm_raw_prob": fmt(llm_raw),
        "llm_norm_prob": fmt(llm_norm),
        "llm_selected_prob": fmt(llm_sel),
        "selected_prob_source": sel_source,
        "market_brier": fmt(market_b),
        "llm_raw_brier": fmt(raw_b),
        "llm_norm_brier": fmt(norm_b),
        "llm_selected_brier": fmt(sel_b),
        "market_log_loss": fmt(market_l),
        "llm_raw_log_loss": fmt(raw_l),
        "llm_norm_log_loss": fmt(norm_l),
        "llm_selected_log_loss": fmt(sel_l),
        "llm_selected_brier_skill_vs_market": fmt(skill),
        "group_mass_flag": sig.get("group_mass_flag", ""),
        "use_normalized_for_analysis": sig.get("use_normalized_for_analysis", ""),
    }


def avg(values: List[Optional[float]]) -> Optional[float]:
    clean = [x for x in values if x is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def bin_name(p: float) -> str:
    low = int(p * 10) / 10
    high = min(1.0, low + 0.1)
    return f"{low:.1f}-{high:.1f}"


def build_summary(rows: List[Dict[str, str]], timestamp_utc: str, mode: str) -> List[Dict[str, str]]:
    out = []
    horizons = sorted(set(r.get("horizon", "") for r in rows))
    sources = [
        ("market", "market_prob", "market_brier", "market_log_loss"),
        ("llm_raw", "llm_raw_prob", "llm_raw_brier", "llm_raw_log_loss"),
        ("llm_norm", "llm_norm_prob", "llm_norm_brier", "llm_norm_log_loss"),
        ("llm_selected", "llm_selected_prob", "llm_selected_brier", "llm_selected_log_loss"),
    ]

    for horizon in horizons:
        subset = [r for r in rows if r.get("horizon") == horizon]
        for source_name, prob_col, brier_col, log_col in sources:
            probs = [safe_float(r.get(prob_col)) for r in subset]
            targets = [safe_float(r.get("target_value")) for r in subset]
            source_rows = [(p, y, r) for p, y, r in zip(probs, targets, subset) if p is not None and y is not None]

            if not source_rows:
                continue

            out.append(
                {
                    "timestamp_utc": timestamp_utc,
                    "mode": mode,
                    "horizon": horizon,
                    "prob_source": source_name,
                    "n": str(len(source_rows)),
                    "avg_brier": fmt(avg([safe_float(r.get(brier_col)) for _, _, r in source_rows])),
                    "avg_log_loss": fmt(avg([safe_float(r.get(log_col)) for _, _, r in source_rows])),
                    "avg_brier_skill_vs_market": fmt(
                        avg([safe_float(r.get("llm_selected_brier_skill_vs_market")) for _, _, r in source_rows])
                    )
                    if source_name == "llm_selected"
                    else "",
                    "avg_market_brier": fmt(avg([safe_float(r.get("market_brier")) for _, _, r in source_rows])),
                    "calibration_bin": "ALL",
                    "bin_n": str(len(source_rows)),
                    "bin_avg_pred": fmt(avg([p for p, _, _ in source_rows])),
                    "bin_avg_target": fmt(avg([y for _, y, _ in source_rows])),
                }
            )

            by_bin: Dict[str, List[Tuple[float, float]]] = {}
            for p, y, _ in source_rows:
                by_bin.setdefault(bin_name(p), []).append((p, y))
            for b, vals in sorted(by_bin.items()):
                out.append(
                    {
                        "timestamp_utc": timestamp_utc,
                        "mode": mode,
                        "horizon": horizon,
                        "prob_source": source_name,
                        "n": str(len(source_rows)),
                        "avg_brier": "",
                        "avg_log_loss": "",
                        "avg_brier_skill_vs_market": "",
                        "avg_market_brier": "",
                        "calibration_bin": b,
                        "bin_n": str(len(vals)),
                        "bin_avg_pred": fmt(avg([p for p, _ in vals])),
                        "bin_avg_target": fmt(avg([y for _, y in vals])),
                    }
                )
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build calibration and Brier/log-loss scoring.")
    p.add_argument("--mode", choices=["soft", "resolution"], default="soft")
    p.add_argument("--signals", default="data/signals/signal_comparison_latest.csv")
    p.add_argument("--markouts", default="data/markouts/markouts_latest.csv")
    p.add_argument("--resolutions", default="data/resolutions.csv")
    p.add_argument("--scores-output", default="data/scoring/calibration_scores_latest.csv")
    p.add_argument("--summary-output", default="data/scoring/calibration_summary_latest.csv")
    p.add_argument("--health-dir", default="data/health")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ts = utc_now()
    timestamp_utc = iso(ts)

    signals = read_csv(Path(args.signals))
    if args.mode == "resolution":
        resolutions = load_resolution_targets(Path(args.resolutions))
        score_rows = build_resolution_scores(signals, resolutions, timestamp_utc)
    else:
        markouts = read_csv(Path(args.markouts))
        score_rows = build_soft_scores(signals, markouts, timestamp_utc)

    summary = build_summary(score_rows, timestamp_utc, args.mode)

    write_csv(Path(args.scores_output), score_rows, SCORE_COLUMNS)
    write_csv(Path(args.summary_output), summary, SUMMARY_COLUMNS)

    health = {
        "timestamp_utc": timestamp_utc,
        "mode": args.mode,
        "signals_input": args.signals,
        "scores_output": args.scores_output,
        "summary_output": args.summary_output,
        "rows_scored": len(score_rows),
        "summary_rows": len(summary),
        "note": "soft mode uses future market price as a proxy target; resolution mode uses final 0/1 outcomes.",
    }
    health_path = Path(args.health_dir) / f"calibration_health_{ts.strftime('%Y%m%dT%H%M%SZ')}.json"
    latest_health = Path(args.health_dir) / "latest_calibration_health.json"
    write_json(health_path, latest_health, health)

    print(f"Rows scored: {len(score_rows)}")
    print(f"Summary rows: {len(summary)}")
    print(f"Wrote scores: {args.scores_output}")
    print(f"Wrote summary: {args.summary_output}")
    print(f"Wrote health: {latest_health}")


if __name__ == "__main__":
    main()
