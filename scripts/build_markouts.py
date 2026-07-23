#!/usr/bin/env python3
"""
scripts/build_markouts.py

PATCH v0.2.2

Fixes markouts returning Rows: 0 by making signal direction inference robust.

Instead of requiring direction columns, this script can infer:
    raw direction from raw_gap
    normalized direction from normalized_gap

Default signal threshold:
    abs(gap) >= 0.10

This means markouts will produce PENDING rows immediately, even before future prices exist.

Run:
    PYTHONPATH=. python scripts/build_markouts.py \
      --signals data/snapshots/signal_comparison_snapshots.csv \
      --prices data/snapshots/signal_input_snapshots.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


OUTPUT_COLUMNS = [
    "markout_id",
    "source_run_id",
    "source_timestamp_utc",
    "horizon",
    "target_timestamp_utc",
    "future_timestamp_utc",
    "markout_status",
    "tracking_id",
    "parent_market_name",
    "primary_outcome_to_track",
    "signal_mode",
    "signal_direction",
    "signal_strength",
    "entry_price",
    "future_price",
    "price_change",
    "directional_markout",
    "entry_fair_value",
    "entry_gap",
    "entry_abs_gap",
    "entry_price_type",
    "future_price_type",
    "entry_liquidity_flags",
    "future_liquidity_flags",
    "group_mass_flag",
    "use_normalized_for_analysis",
]


def first_nonempty(row: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key, "")
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except Exception:
        return None


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


def parse_time(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        out = dt.datetime.fromisoformat(text)
        if out.tzinfo is None:
            out = out.replace(tzinfo=dt.timezone.utc)
        return out.astimezone(dt.timezone.utc)
    except Exception:
        return None


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def fmt(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.8f}".rstrip("0").rstrip(".")


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, latest_path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def index_prices(price_rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    idx: Dict[str, List[Dict[str, str]]] = {}
    for r in price_rows:
        if r.get("signal_ready", "").lower() != "true":
            continue
        tid = r.get("tracking_id", "")
        ts = parse_time(r.get("timestamp_utc", ""))
        price = safe_float(r.get("comparison_price", ""))
        if not tid or ts is None or price is None:
            continue
        r["_ts"] = ts  # type: ignore
        idx.setdefault(tid, []).append(r)

    for rows in idx.values():
        rows.sort(key=lambda r: r["_ts"])  # type: ignore

    return idx


def find_nearest_future(
    rows: List[Dict[str, str]], source_ts: dt.datetime, target: dt.datetime, tolerance_hours: float
) -> Optional[Dict[str, str]]:
    tolerance = dt.timedelta(hours=tolerance_hours)
    candidates = []
    for r in rows:
        ts = r["_ts"]  # type: ignore

        # Must be meaningfully after source snapshot.
        if ts <= source_ts + dt.timedelta(minutes=1):
            continue

        delta = abs(ts - target)
        if delta <= tolerance:
            candidates.append((delta, ts, r))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def row_signal_fields(
    signal: Dict[str, str], mode: str, min_abs_gap: float
) -> Tuple[str, str, Optional[float], Optional[float], Optional[float]]:
    if mode == "normalized":
        fair_value = safe_float(first_nonempty(signal, "fair_value_group_norm", "normalized_fair_value"))
        gap = safe_float(first_nonempty(signal, "normalized_gap"))
        abs_gap = safe_float(first_nonempty(signal, "normalized_abs_gap"))
        if abs_gap is None and gap is not None:
            abs_gap = abs(gap)

        direction = first_nonempty(signal, "normalized_signal_direction", "normalized_direction")
        if not direction:
            direction = infer_direction(gap, min_abs_gap)

        strength = first_nonempty(signal, "normalized_signal_strength", "normalized_strength")
        if not strength:
            strength = infer_strength(abs_gap)

        return direction, strength, fair_value, gap, abs_gap

    fair_value = safe_float(first_nonempty(signal, "fair_value", "raw_fair_value"))
    gap = safe_float(first_nonempty(signal, "raw_gap"))
    abs_gap = safe_float(first_nonempty(signal, "raw_abs_gap"))
    if abs_gap is None and gap is not None:
        abs_gap = abs(gap)

    direction = first_nonempty(signal, "raw_signal_direction", "raw_direction")
    if not direction:
        direction = infer_direction(gap, min_abs_gap)

    strength = first_nonempty(signal, "raw_signal_strength", "raw_strength")
    if not strength:
        strength = infer_strength(abs_gap)

    return direction, strength, fair_value, gap, abs_gap


def build_markout_rows(
    signal_rows: List[Dict[str, str]],
    price_index: Dict[str, List[Dict[str, str]]],
    horizons: List[Tuple[str, dt.timedelta]],
    tolerance_hours: float,
    signal_modes: List[str],
    min_abs_gap: float,
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    skipped_no_timestamp = 0
    skipped_no_entry_price = 0
    skipped_no_tid = 0
    skipped_no_signal = 0

    for sig in signal_rows:
        source_ts = parse_time(sig.get("timestamp_utc", ""))
        if source_ts is None:
            skipped_no_timestamp += 1
            continue

        entry_price = safe_float(sig.get("comparison_price", ""))
        if entry_price is None:
            skipped_no_entry_price += 1
            continue

        tid = sig.get("tracking_id", "")
        if not tid:
            skipped_no_tid += 1
            continue

        for mode in signal_modes:
            direction, strength, fair_value, gap, abs_gap = row_signal_fields(sig, mode, min_abs_gap)
            if direction not in {"LLM_HIGHER", "MARKET_HIGHER"}:
                skipped_no_signal += 1
                continue

            for horizon_name, delta in horizons:
                target_ts = source_ts + delta
                future = find_nearest_future(price_index.get(tid, []), source_ts, target_ts, tolerance_hours)

                row = {col: "" for col in OUTPUT_COLUMNS}
                row.update(
                    {
                        "markout_id": f"{sig.get('run_id', '')}:{tid}:{mode}:{horizon_name}",
                        "source_run_id": sig.get("run_id", ""),
                        "source_timestamp_utc": sig.get("timestamp_utc", ""),
                        "horizon": horizon_name,
                        "target_timestamp_utc": iso(target_ts),
                        "tracking_id": tid,
                        "parent_market_name": sig.get("parent_market_name", ""),
                        "primary_outcome_to_track": sig.get("primary_outcome_to_track", ""),
                        "signal_mode": mode,
                        "signal_direction": direction,
                        "signal_strength": strength,
                        "entry_price": fmt(entry_price),
                        "entry_fair_value": fmt(fair_value),
                        "entry_gap": fmt(gap),
                        "entry_abs_gap": fmt(abs_gap),
                        "entry_price_type": sig.get("price_type", ""),
                        "entry_liquidity_flags": sig.get("liquidity_flags", ""),
                        "group_mass_flag": sig.get("group_mass_flag", ""),
                        "use_normalized_for_analysis": sig.get("use_normalized_for_analysis", ""),
                    }
                )

                if future is None:
                    row["markout_status"] = "PENDING"
                    out.append(row)
                    continue

                future_price = safe_float(future.get("comparison_price", ""))
                if future_price is None:
                    row["markout_status"] = "NO_FUTURE_PRICE"
                    out.append(row)
                    continue

                price_change = future_price - entry_price
                directional = price_change if direction == "LLM_HIGHER" else -price_change

                row.update(
                    {
                        "future_timestamp_utc": future.get("timestamp_utc", ""),
                        "markout_status": "READY",
                        "future_price": fmt(future_price),
                        "price_change": fmt(price_change),
                        "directional_markout": fmt(directional),
                        "future_price_type": future.get("price_type", ""),
                        "future_liquidity_flags": future.get("liquidity_flags", ""),
                    }
                )
                out.append(row)

    # Attach debug counters to function for health report.
    build_markout_rows.debug = {
        "skipped_no_timestamp": skipped_no_timestamp,
        "skipped_no_entry_price": skipped_no_entry_price,
        "skipped_no_tracking_id": skipped_no_tid,
        "skipped_no_signal_mode_rows": skipped_no_signal,
    }

    return out


def parse_horizons(values: List[str]) -> List[Tuple[str, dt.timedelta]]:
    out = []
    for v in values:
        v = v.strip().lower()
        if v.endswith("d"):
            out.append((v, dt.timedelta(days=int(v[:-1]))))
        elif v.endswith("h"):
            out.append((v, dt.timedelta(hours=int(v[:-1]))))
        else:
            raise ValueError(f"Unsupported horizon: {v}. Use e.g. 1d, 7d, 30d, 12h.")
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build signal markout analysis.")
    p.add_argument("--signals", default="data/snapshots/signal_comparison_snapshots.csv")
    p.add_argument("--prices", default="data/snapshots/signal_input_snapshots.csv")
    p.add_argument("--latest-output", default="data/markouts/markouts_latest.csv")
    p.add_argument("--snapshot-dir", default="data/markouts")
    p.add_argument("--health-dir", default="data/health")
    p.add_argument("--horizons", nargs="+", default=["1d", "7d", "30d"])
    p.add_argument("--tolerance-hours", type=float, default=12)
    p.add_argument("--signal-modes", nargs="+", default=["raw", "normalized"], choices=["raw", "normalized"])
    p.add_argument("--min-abs-gap", type=float, default=0.10)
    return p.parse_args()


def main() -> None:
    args = parse_args()

    signals = read_csv(Path(args.signals))
    prices = read_csv(Path(args.prices))
    price_idx = index_prices(prices)
    horizons = parse_horizons(args.horizons)

    rows = build_markout_rows(
        signals,
        price_idx,
        horizons,
        args.tolerance_hours,
        args.signal_modes,
        args.min_abs_gap,
    )

    ts = utc_now()
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    snapshot_output = Path(args.snapshot_dir) / f"markouts_{stamp}.csv"

    write_csv(Path(args.latest_output), rows, OUTPUT_COLUMNS)
    write_csv(snapshot_output, rows, OUTPUT_COLUMNS)

    counts: Dict[str, int] = {}
    by_horizon: Dict[str, Dict[str, int]] = {}
    by_mode: Dict[str, Dict[str, int]] = {}
    directional_ready: List[float] = []

    for r in rows:
        status = r["markout_status"]
        counts[status] = counts.get(status, 0) + 1

        h = r["horizon"]
        by_horizon.setdefault(h, {})
        by_horizon[h][status] = by_horizon[h].get(status, 0) + 1

        mode = r["signal_mode"]
        by_mode.setdefault(mode, {})
        by_mode[mode][status] = by_mode[mode].get(status, 0) + 1

        if status == "READY":
            val = safe_float(r.get("directional_markout"))
            if val is not None:
                directional_ready.append(val)

    report = {
        "timestamp_utc": iso(ts),
        "signals_input": args.signals,
        "prices_input": args.prices,
        "latest_output": args.latest_output,
        "snapshot_output": str(snapshot_output),
        "min_abs_gap": args.min_abs_gap,
        "signal_rows_input": len(signals),
        "price_rows_input": len(prices),
        "rows_total": len(rows),
        "status_counts": counts,
        "status_by_horizon": by_horizon,
        "status_by_mode": by_mode,
        "debug": getattr(build_markout_rows, "debug", {}),
        "avg_directional_markout_ready": round(sum(directional_ready) / len(directional_ready), 8)
        if directional_ready
        else None,
        "positive_directional_markout_rate": round(
            sum(1 for x in directional_ready if x > 0) / len(directional_ready), 4
        )
        if directional_ready
        else None,
        "sample_ready": [r for r in rows if r["markout_status"] == "READY"][:10],
        "sample_pending": [r for r in rows if r["markout_status"] == "PENDING"][:10],
    }

    health_path = Path(args.health_dir) / f"markout_health_{stamp}.json"
    latest_health = Path(args.health_dir) / "latest_markout_health.json"
    write_json(health_path, latest_health, report)

    print(f"Signal rows input: {len(signals)}")
    print(f"Price rows input: {len(prices)}")
    print(f"Rows: {len(rows)}")
    print(f"Status counts: {counts}")
    print(f"By horizon: {by_horizon}")
    print(f"By mode: {by_mode}")
    print(f"Debug: {report['debug']}")
    print(f"Wrote latest: {args.latest_output}")
    print(f"Wrote health: {latest_health}")


if __name__ == "__main__":
    main()
