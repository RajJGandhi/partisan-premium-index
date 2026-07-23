#!/usr/bin/env python3
"""
scripts/build_signal_input_snapshot.py

Converts raw CLOB order-book snapshots into clean signal-input rows.

Input:
    data/orderbook_check.csv

Outputs:
    data/signal_inputs/signal_input_latest.csv
    data/signal_inputs/signal_input_<snapshot_id>.csv
    data/snapshots/signal_input_snapshots.csv
    data/health/latest_signal_input_health.json
    data/health/signal_input_health_<snapshot_id>.json

Purpose:
    The order-book checker records raw executable market microstructure:
        best_bid, best_ask, mid, spread, depth, status

    This script converts that into the one clean price field the experiment uses:
        comparison_price

Price policy:
    If bid and ask exist:
        comparison_price = mid
        price_type = mid

    If no bid but ask exists:
        comparison_price = best_ask
        price_type = ask_only

    If no ask:
        row is not signal-ready.

Run:
    PYTHONPATH=. python scripts/build_signal_input_snapshot.py \
      --input data/orderbook_check.csv

Optional:
    PYTHONPATH=. python scripts/build_signal_input_snapshot.py \
      --input data/orderbook_check.csv \
      --wide-spread-threshold 0.10 \
      --thin-depth-threshold 10
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


OUTPUT_COLUMNS = [
    "signal_input_id",
    "snapshot_id",
    "timestamp_utc",
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
    "token_label",
    "token_id",
    "outcome_contract_question",
    "source_orderbook_status",
    "best_bid",
    "best_ask",
    "mid",
    "spread",
    "comparison_price",
    "price_type",
    "executable_buy_price",
    "executable_sell_price",
    "bid_size_at_best",
    "ask_size_at_best",
    "bid_depth_1c",
    "bid_depth_3c",
    "bid_depth_5c",
    "ask_depth_1c",
    "ask_depth_3c",
    "ask_depth_5c",
    "total_bid_depth",
    "total_ask_depth",
    "liquidity_flags",
    "signal_ready",
    "skip_reason",
    "book_timestamp",
    "book_hash",
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


def determine_price_fields(
    row: Dict[str, str],
    wide_spread_threshold: float,
    thin_depth_threshold: float,
) -> Dict[str, str]:
    status = row.get("status", "")
    best_bid = safe_float(row.get("best_bid"))
    best_ask = safe_float(row.get("best_ask"))
    mid = safe_float(row.get("mid"))
    spread = safe_float(row.get("spread"))

    bid_depth_3c = safe_float(row.get("bid_depth_3c"))
    ask_depth_3c = safe_float(row.get("ask_depth_3c"))

    flags: List[str] = []
    signal_ready = "false"
    skip_reason = ""
    comparison_price: Optional[float] = None
    price_type = ""

    # Use best ask as executable buy price whenever it exists.
    executable_buy_price = best_ask
    executable_sell_price = best_bid

    if best_bid is not None and best_ask is not None:
        comparison_price = mid if mid is not None else (best_bid + best_ask) / 2
        price_type = "mid"
        signal_ready = "true"

    elif best_bid is None and best_ask is not None:
        comparison_price = best_ask
        price_type = "ask_only"
        signal_ready = "true"
        flags.append("no_bids")

    elif best_ask is None:
        signal_ready = "false"
        price_type = "no_executable_buy_price"
        skip_reason = "missing_best_ask"
        if best_bid is None:
            flags.append("empty_or_no_two_sided_book")
        else:
            flags.append("no_asks")

    if status and status != "OK":
        flags.append(status.lower())

    if spread is not None and spread >= wide_spread_threshold:
        flags.append("wide_spread")

    if bid_depth_3c is not None and bid_depth_3c < thin_depth_threshold:
        flags.append("thin_bid")

    if ask_depth_3c is not None and ask_depth_3c < thin_depth_threshold:
        flags.append("thin_ask")

    if signal_ready == "true" and comparison_price is not None:
        if comparison_price < 0 or comparison_price > 1:
            signal_ready = "false"
            skip_reason = "comparison_price_out_of_bounds"
            flags.append("bad_price_bounds")

    # De-dupe flags while preserving order.
    deduped_flags: List[str] = []
    seen = set()
    for flag in flags:
        if flag and flag not in seen:
            seen.add(flag)
            deduped_flags.append(flag)

    return {
        "comparison_price": fmt(comparison_price),
        "price_type": price_type,
        "executable_buy_price": fmt(executable_buy_price),
        "executable_sell_price": fmt(executable_sell_price),
        "liquidity_flags": "|".join(deduped_flags),
        "signal_ready": signal_ready,
        "skip_reason": skip_reason,
    }


def convert_row(
    row: Dict[str, str],
    row_index: int,
    wide_spread_threshold: float,
    thin_depth_threshold: float,
) -> Dict[str, str]:
    price_fields = determine_price_fields(
        row,
        wide_spread_threshold=wide_spread_threshold,
        thin_depth_threshold=thin_depth_threshold,
    )

    snapshot_id = row.get("snapshot_id", "")
    tracking_id = row.get("tracking_id", "")

    out = {col: "" for col in OUTPUT_COLUMNS}
    out.update(
        {
            "signal_input_id": f"{snapshot_id}:{tracking_id}:{row.get('token_label', 'YES')}:{row_index}",
            "snapshot_id": snapshot_id,
            "timestamp_utc": row.get("timestamp_utc", ""),
            "tracking_id": tracking_id,
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
            "token_label": row.get("token_label", ""),
            "token_id": row.get("token_id", ""),
            "outcome_contract_question": row.get("outcome_contract_question", ""),
            "source_orderbook_status": row.get("status", ""),
            "best_bid": row.get("best_bid", ""),
            "best_ask": row.get("best_ask", ""),
            "mid": row.get("mid", ""),
            "spread": row.get("spread", ""),
            "bid_size_at_best": row.get("bid_size_at_best", ""),
            "ask_size_at_best": row.get("ask_size_at_best", ""),
            "bid_depth_1c": row.get("bid_depth_1c", ""),
            "bid_depth_3c": row.get("bid_depth_3c", ""),
            "bid_depth_5c": row.get("bid_depth_5c", ""),
            "ask_depth_1c": row.get("ask_depth_1c", ""),
            "ask_depth_3c": row.get("ask_depth_3c", ""),
            "ask_depth_5c": row.get("ask_depth_5c", ""),
            "total_bid_depth": row.get("total_bid_depth", ""),
            "total_ask_depth": row.get("total_ask_depth", ""),
            "book_timestamp": row.get("book_timestamp", ""),
            "book_hash": row.get("book_hash", ""),
        }
    )
    out.update(price_fields)
    return out


def write_health_report(path: Path, latest_path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def build_health_report(
    rows: List[Dict[str, str]],
    input_path: str,
    latest_output_path: str,
    snapshot_output_path: str,
    append_output_path: str,
    wide_spread_threshold: float,
    thin_depth_threshold: float,
) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    price_type_counts: Dict[str, int] = {}
    flag_counts: Dict[str, int] = {}

    ready_rows = [r for r in rows if r["signal_ready"] == "true"]
    skipped_rows = [r for r in rows if r["signal_ready"] != "true"]

    for row in rows:
        status_counts[row.get("source_orderbook_status", "")] = (
            status_counts.get(row.get("source_orderbook_status", ""), 0) + 1
        )
        price_type_counts[row.get("price_type", "")] = price_type_counts.get(row.get("price_type", ""), 0) + 1
        for flag in row.get("liquidity_flags", "").split("|"):
            if flag:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1

    comparison_prices = [safe_float(r["comparison_price"]) for r in ready_rows]
    comparison_prices = [x for x in comparison_prices if x is not None]

    snapshot_id = rows[0]["snapshot_id"] if rows else ""
    timestamp_utc = rows[0]["timestamp_utc"] if rows else iso_utc()

    return {
        "snapshot_id": snapshot_id,
        "timestamp_utc": timestamp_utc,
        "input_path": input_path,
        "latest_output_path": latest_output_path,
        "snapshot_output_path": snapshot_output_path,
        "append_output_path": append_output_path,
        "wide_spread_threshold": wide_spread_threshold,
        "thin_depth_threshold": thin_depth_threshold,
        "rows_total": len(rows),
        "signal_ready_count": len(ready_rows),
        "skipped_count": len(skipped_rows),
        "signal_ready_rate": round(len(ready_rows) / max(1, len(rows)), 4),
        "source_orderbook_status_counts": status_counts,
        "price_type_counts": price_type_counts,
        "liquidity_flag_counts": flag_counts,
        "avg_comparison_price": round(sum(comparison_prices) / len(comparison_prices), 6)
        if comparison_prices
        else None,
        "min_comparison_price": round(min(comparison_prices), 6) if comparison_prices else None,
        "max_comparison_price": round(max(comparison_prices), 6) if comparison_prices else None,
        "sample_skipped_rows": [
            {
                "tracking_id": r.get("tracking_id"),
                "parent_market_name": r.get("parent_market_name"),
                "outcome": r.get("primary_outcome_to_track"),
                "source_status": r.get("source_orderbook_status"),
                "price_type": r.get("price_type"),
                "skip_reason": r.get("skip_reason"),
                "flags": r.get("liquidity_flags"),
            }
            for r in skipped_rows[:25]
        ],
        "sample_flagged_rows": [
            {
                "tracking_id": r.get("tracking_id"),
                "parent_market_name": r.get("parent_market_name"),
                "outcome": r.get("primary_outcome_to_track"),
                "comparison_price": r.get("comparison_price"),
                "price_type": r.get("price_type"),
                "flags": r.get("liquidity_flags"),
            }
            for r in rows
            if r.get("liquidity_flags")
        ][:25],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build signal-input rows from CLOB order-book snapshots.")
    parser.add_argument("--input", default="data/orderbook_check.csv")
    parser.add_argument("--latest-output", default="data/signal_inputs/signal_input_latest.csv")
    parser.add_argument("--snapshot-dir", default="data/signal_inputs")
    parser.add_argument("--append-output", default="data/snapshots/signal_input_snapshots.csv")
    parser.add_argument("--health-dir", default="data/health")
    parser.add_argument("--wide-spread-threshold", type=float, default=0.10)
    parser.add_argument("--thin-depth-threshold", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    source_rows = read_csv(Path(args.input))
    converted = [
        convert_row(
            row,
            row_index=i,
            wide_spread_threshold=args.wide_spread_threshold,
            thin_depth_threshold=args.thin_depth_threshold,
        )
        for i, row in enumerate(source_rows, start=1)
    ]

    snapshot_id = converted[0]["snapshot_id"] if converted else utc_now().strftime("%Y%m%dT%H%M%SZ")

    latest_output = Path(args.latest_output)
    snapshot_output = Path(args.snapshot_dir) / f"signal_input_{snapshot_id}.csv"
    append_output = Path(args.append_output)

    write_csv(latest_output, converted, OUTPUT_COLUMNS, append=False)
    write_csv(snapshot_output, converted, OUTPUT_COLUMNS, append=False)
    write_csv(append_output, converted, OUTPUT_COLUMNS, append=True)

    health_report = build_health_report(
        converted,
        input_path=args.input,
        latest_output_path=str(latest_output),
        snapshot_output_path=str(snapshot_output),
        append_output_path=str(append_output),
        wide_spread_threshold=args.wide_spread_threshold,
        thin_depth_threshold=args.thin_depth_threshold,
    )

    health_path = Path(args.health_dir) / f"signal_input_health_{snapshot_id}.json"
    latest_health_path = Path(args.health_dir) / "latest_signal_input_health.json"
    write_health_report(health_path, latest_health_path, health_report)

    print(f"Rows total: {health_report['rows_total']}")
    print(f"Signal-ready: {health_report['signal_ready_count']}")
    print(f"Skipped: {health_report['skipped_count']}")
    print(f"Signal-ready rate: {health_report['signal_ready_rate']}")

    print("\\nPrice types:")
    for k, v in sorted(health_report["price_type_counts"].items()):
        print(f"  {k}: {v}")

    print("\\nLiquidity flags:")
    for k, v in sorted(health_report["liquidity_flag_counts"].items()):
        print(f"  {k}: {v}")

    print("\\nWrote:")
    print(f"  Latest signal input: {latest_output}")
    print(f"  Timestamped signal input: {snapshot_output}")
    print(f"  Append-only signal inputs: {append_output}")
    print(f"  Latest health: {latest_health_path}")
    print(f"  Timestamped health: {health_path}")

    if health_report["signal_ready_count"] == 0:
        raise SystemExit("No signal-ready rows produced.")


if __name__ == "__main__":
    main()
