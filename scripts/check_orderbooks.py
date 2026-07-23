#!/usr/bin/env python3
"""
scripts/check_orderbooks.py

Read-only Polymarket CLOB order-book checker for Reality Spread.

Input:
    data/tracked_markets_final.csv

Outputs:
    data/orderbook_check.csv
    data/snapshots/orderbook_snapshots.csv
    data/snapshots/orderbook_snapshots_<timestamp>.csv
    data/health/latest_orderbook_health.json

Default:
    Snapshots the YES token for each option-level contract.

Run:
    pip install requests

    PYTHONPATH=. python scripts/check_orderbooks.py \
      --input data/tracked_markets_final.csv \
      --output data/orderbook_check.csv

Test:
    PYTHONPATH=. python scripts/check_orderbooks.py \
      --input data/tracked_markets_final.csv \
      --output data/orderbook_check.csv \
      --limit 10
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"

SNAPSHOT_COLUMNS = [
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
    "token_index",
    "outcome_contract_question",
    "best_bid",
    "best_ask",
    "mid",
    "spread",
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
    "book_timestamp",
    "book_hash",
    "min_order_size",
    "tick_size",
    "neg_risk",
    "raw_bids_count",
    "raw_asks_count",
    "status",
    "error",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts: Optional[dt.datetime] = None) -> str:
    return (ts or utc_now()).isoformat().replace("+00:00", "Z")


def safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict, bool, int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        val = float(str(value))
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except Exception:
        return None


def fmt(value: Optional[float], decimals: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".")


def parse_order_levels(levels: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(levels, list):
        return out

    for item in levels:
        price = None
        size = None
        if isinstance(item, dict):
            price = safe_float(item.get("price"))
            size = safe_float(item.get("size"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price = safe_float(item[0])
            size = safe_float(item[1])

        if price is None or size is None:
            continue
        if 0 <= price <= 1 and size >= 0:
            out.append((price, size))

    return out


def best_bid(levels: List[Tuple[float, float]]) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    return max(levels, key=lambda x: x[0])


def best_ask(levels: List[Tuple[float, float]]) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    return min(levels, key=lambda x: x[0])


def depth_within(
    levels: List[Tuple[float, float]], reference_price: Optional[float], side: str, cents: float
) -> Optional[float]:
    if reference_price is None:
        return None
    if side == "bid":
        floor = max(0.0, reference_price - cents)
        return sum(size for price, size in levels if price >= floor)
    if side == "ask":
        ceiling = min(1.0, reference_price + cents)
        return sum(size for price, size in levels if price <= ceiling)
    raise ValueError(f"Unknown side: {side}")


def total_depth(levels: List[Tuple[float, float]]) -> float:
    return sum(size for _, size in levels)


def choose_tokens(row: Dict[str, str], include_no: bool = False) -> List[Tuple[str, str, int]]:
    yes = str(row.get("yes_token_id", "")).strip()
    no = str(row.get("no_token_id", "")).strip()

    tokens: List[Tuple[str, str, int]] = []
    if yes:
        tokens.append(("YES", yes, 0))
    if include_no and no:
        tokens.append(("NO", no, 1))

    if not tokens:
        ids = safe_json_loads(row.get("clob_token_ids_json"))
        if isinstance(ids, list):
            if len(ids) >= 1 and str(ids[0]).strip():
                tokens.append(("YES", str(ids[0]).strip(), 0))
            if include_no and len(ids) >= 2 and str(ids[1]).strip():
                tokens.append(("NO", str(ids[1]).strip(), 1))

    return tokens


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: int = 25,
    retries: int = 3,
) -> Any:
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            if method.upper() == "GET":
                resp = session.get(url, params=params, timeout=timeout)
            elif method.upper() == "POST":
                resp = session.post(url, json=json_body, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code == 429:
                last_error = "429 rate limited"
                time.sleep(1.5 * attempt)
                continue

            if resp.status_code in {400, 404}:
                try:
                    return {"_error": resp.json()}
                except Exception:
                    return {"_error": resp.text}

            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(0.5 * attempt)
            else:
                return {"_error": last_error}

    return {"_error": last_error or "unknown_error"}


def fetch_book_single(session: requests.Session, clob_base_url: str, token_id: str) -> Dict[str, Any]:
    base = clob_base_url.rstrip("/")
    data = request_json(session, "GET", f"{base}/book", params={"token_id": token_id})
    if isinstance(data, dict):
        return data
    return {"_error": "unexpected_book_response"}


def book_token_id(book: Dict[str, Any]) -> str:
    return str(book.get("asset_id") or book.get("token_id") or book.get("tokenID") or book.get("assetId") or "").strip()


def fetch_books_batch(
    session: requests.Session, clob_base_url: str, token_ids: Sequence[str], batch_size: int = 100
) -> Dict[str, Dict[str, Any]]:
    """
    Correctness-first book fetcher.

    We intentionally fetch one token at a time by default. The previous batch
    path could silently mis-assign books to token IDs if POST /books returned
    results in a different order or without a reliable echoed asset_id.

    188 GET calls twice daily is acceptable for v0.1 and is much safer than
    corrupting the price side of the experiment.
    """
    out: Dict[str, Dict[str, Any]] = {}

    for i, token_id in enumerate(token_ids, start=1):
        book = fetch_book_single(session, clob_base_url, token_id)

        if isinstance(book, dict) and "_error" not in book:
            returned_id = book_token_id(book)
            if returned_id and returned_id != token_id:
                book = {"_error": f"token_id_mismatch requested={token_id} returned={returned_id}"}

        out[token_id] = book

        if i % 25 == 0:
            print(f"  fetched {i}/{len(token_ids)} books")
        time.sleep(0.05)

    return out


def build_snapshot_row(
    snapshot_id: str,
    timestamp_utc: str,
    source_row: Dict[str, str],
    token_label: str,
    token_id: str,
    token_index: int,
    book: Dict[str, Any],
) -> Dict[str, str]:
    row = {col: "" for col in SNAPSHOT_COLUMNS}
    row.update(
        {
            "snapshot_id": snapshot_id,
            "timestamp_utc": timestamp_utc,
            "tracking_id": source_row.get("tracking_id", ""),
            "parent_market_name": source_row.get("parent_market_name", ""),
            "market_name": source_row.get("market_name", ""),
            "primary_outcome_to_track": source_row.get("primary_outcome_to_track", ""),
            "region": source_row.get("region", ""),
            "bucket": source_row.get("bucket", ""),
            "system_type": source_row.get("system_type", ""),
            "underlying_event_group": source_row.get("underlying_event_group", ""),
            "gamma_market_id": source_row.get("gamma_market_id", ""),
            "condition_id": source_row.get("condition_id", ""),
            "exact_polymarket_slug": source_row.get("exact_polymarket_slug", ""),
            "market_url": source_row.get("market_url", ""),
            "token_label": token_label,
            "token_id": token_id,
            "token_index": str(token_index),
            "outcome_contract_question": source_row.get("question", ""),
        }
    )

    if not isinstance(book, dict):
        row["status"] = "ERROR"
        row["error"] = "book_not_dict"
        return row

    if "_error" in book:
        row["status"] = "ERROR"
        row["error"] = str(book.get("_error"))
        return row

    bids = parse_order_levels(book.get("bids"))
    asks = parse_order_levels(book.get("asks"))

    bid_price, bid_size = best_bid(bids)
    ask_price, ask_size = best_ask(asks)

    mid = None
    spread = None
    if bid_price is not None and ask_price is not None:
        mid = (bid_price + ask_price) / 2
        spread = ask_price - bid_price

    status = "OK"
    error = ""

    if bid_price is None and ask_price is None:
        status = "EMPTY_BOOK"
        error = "missing_bids_and_asks"
    elif bid_price is None:
        status = "NO_BIDS"
        error = "missing_bids"
    elif ask_price is None:
        status = "NO_ASKS"
        error = "missing_asks"
    elif spread is not None and spread < -0.000001:
        status = "CROSSED_OR_BAD_BOOK"
        error = f"negative_spread={spread}"

    row.update(
        {
            "best_bid": fmt(bid_price),
            "best_ask": fmt(ask_price),
            "mid": fmt(mid),
            "spread": fmt(spread),
            "bid_size_at_best": fmt(bid_size, 4),
            "ask_size_at_best": fmt(ask_size, 4),
            "bid_depth_1c": fmt(depth_within(bids, bid_price, "bid", 0.01), 4),
            "bid_depth_3c": fmt(depth_within(bids, bid_price, "bid", 0.03), 4),
            "bid_depth_5c": fmt(depth_within(bids, bid_price, "bid", 0.05), 4),
            "ask_depth_1c": fmt(depth_within(asks, ask_price, "ask", 0.01), 4),
            "ask_depth_3c": fmt(depth_within(asks, ask_price, "ask", 0.03), 4),
            "ask_depth_5c": fmt(depth_within(asks, ask_price, "ask", 0.05), 4),
            "total_bid_depth": fmt(total_depth(bids), 4),
            "total_ask_depth": fmt(total_depth(asks), 4),
            "book_timestamp": str(book.get("timestamp", "")),
            "book_hash": str(book.get("hash", "")),
            "min_order_size": str(book.get("min_order_size", "")),
            "tick_size": str(book.get("tick_size", "")),
            "neg_risk": str(book.get("neg_risk", "")),
            "raw_bids_count": str(len(bids)),
            "raw_asks_count": str(len(asks)),
            "status": status,
            "error": error,
        }
    )

    return row


def read_tracked_markets(path: Path, limit: Optional[int] = None) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit is not None:
        rows = rows[:limit]
    return rows


def write_csv(path: Path, rows: List[Dict[str, str]], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SNAPSHOT_COLUMNS, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def write_health_report(path: Path, latest_path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Polymarket CLOB order-book snapshots for tracked markets.")
    parser.add_argument("--input", default="data/tracked_markets_final.csv")
    parser.add_argument("--output", default="data/orderbook_check.csv")
    parser.add_argument("--append-output", default="data/snapshots/orderbook_snapshots.csv")
    parser.add_argument("--snapshot-dir", default="data/snapshots")
    parser.add_argument("--health-dir", default="data/health")
    parser.add_argument("--clob-base-url", default=DEFAULT_CLOB_BASE_URL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-no", action="store_true", help="Also snapshot NO token books.")
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    now = utc_now()
    snapshot_id = now.strftime("%Y%m%dT%H%M%SZ")
    timestamp_utc = iso_utc(now)

    rows = read_tracked_markets(Path(args.input), limit=args.limit)

    token_records: List[Tuple[Dict[str, str], str, str, int]] = []
    missing_token_rows: List[Dict[str, str]] = []

    for source_row in rows:
        tokens = choose_tokens(source_row, include_no=args.include_no)
        if not tokens:
            missing_token_rows.append(source_row)
        for token_label, token_id, token_index in tokens:
            token_records.append((source_row, token_label, token_id, token_index))

    token_ids = list(dict.fromkeys(token_id for _, _, token_id, _ in token_records))

    print(f"Snapshot ID: {snapshot_id}")
    print(f"Input market rows: {len(rows)}")
    print(f"Token records to fetch: {len(token_records)}")
    print(f"Unique token IDs: {len(token_ids)}")
    print(f"Missing-token source rows: {len(missing_token_rows)}")
    print("Fetch mode: correctness-first single-token /book requests")

    session = requests.Session()
    books_by_token = fetch_books_batch(session, args.clob_base_url, token_ids, batch_size=args.batch_size)

    snapshot_rows: List[Dict[str, str]] = []

    for source_row, token_label, token_id, token_index in token_records:
        snapshot_rows.append(
            build_snapshot_row(
                snapshot_id=snapshot_id,
                timestamp_utc=timestamp_utc,
                source_row=source_row,
                token_label=token_label,
                token_id=token_id,
                token_index=token_index,
                book=books_by_token.get(token_id, {"_error": "missing_book_response"}),
            )
        )

    for source_row in missing_token_rows:
        row = {col: "" for col in SNAPSHOT_COLUMNS}
        row.update(
            {
                "snapshot_id": snapshot_id,
                "timestamp_utc": timestamp_utc,
                "tracking_id": source_row.get("tracking_id", ""),
                "parent_market_name": source_row.get("parent_market_name", ""),
                "market_name": source_row.get("market_name", ""),
                "primary_outcome_to_track": source_row.get("primary_outcome_to_track", ""),
                "gamma_market_id": source_row.get("gamma_market_id", ""),
                "condition_id": source_row.get("condition_id", ""),
                "exact_polymarket_slug": source_row.get("exact_polymarket_slug", ""),
                "market_url": source_row.get("market_url", ""),
                "status": "MISSING_TOKEN_ID",
                "error": "No yes_token_id/no_token_id/clob_token_ids_json found",
            }
        )
        snapshot_rows.append(row)

    latest_path = Path(args.output)
    snapshot_path = Path(args.snapshot_dir) / f"orderbook_snapshots_{snapshot_id}.csv"
    append_path = Path(args.append_output)
    health_path = Path(args.health_dir) / f"orderbook_health_{snapshot_id}.json"
    latest_health_path = Path(args.health_dir) / "latest_orderbook_health.json"

    write_csv(latest_path, snapshot_rows, append=False)
    write_csv(snapshot_path, snapshot_rows, append=False)
    write_csv(append_path, snapshot_rows, append=True)

    status_counts: Dict[str, int] = {}
    spreads = []

    for row in snapshot_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        if row["status"] == "OK":
            spread = safe_float(row.get("spread"))
            if spread is not None:
                spreads.append(spread)

    ok_rows = [r for r in snapshot_rows if r["status"] == "OK"]
    report = {
        "snapshot_id": snapshot_id,
        "timestamp_utc": timestamp_utc,
        "input_path": args.input,
        "output_path": args.output,
        "include_no": args.include_no,
        "source_market_rows": len(rows),
        "snapshot_token_rows": len(snapshot_rows),
        "unique_token_ids": len(token_ids),
        "status_counts": status_counts,
        "ok_rate": round(status_counts.get("OK", 0) / max(1, len(snapshot_rows)), 4),
        "avg_spread": round(sum(spreads) / len(spreads), 6) if spreads else None,
        "max_spread": round(max(spreads), 6) if spreads else None,
        "wide_spread_count_ge_10c": sum(1 for r in ok_rows if (safe_float(r.get("spread")) or 0) >= 0.10),
        "thin_ask_depth_3c_lt_10_count": sum(1 for r in ok_rows if (safe_float(r.get("ask_depth_3c")) or 0) < 10),
        "thin_bid_depth_3c_lt_10_count": sum(1 for r in ok_rows if (safe_float(r.get("bid_depth_3c")) or 0) < 10),
        "sample_problem_rows": [
            {
                "tracking_id": r.get("tracking_id"),
                "parent_market_name": r.get("parent_market_name"),
                "outcome": r.get("primary_outcome_to_track"),
                "token_label": r.get("token_label"),
                "status": r.get("status"),
                "error": r.get("error"),
            }
            for r in snapshot_rows
            if r["status"] != "OK"
        ][:25],
    }

    write_health_report(health_path, latest_health_path, report)

    print("\nStatus counts:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    print(f"\nOK rate: {report['ok_rate']}")
    print(f"Avg spread: {report['avg_spread']}")
    print(f"Max spread: {report['max_spread']}")
    print(f"Wide spreads >= 10c: {report['wide_spread_count_ge_10c']}")
    print(f"Thin ask depth within 3c < 10: {report['thin_ask_depth_3c_lt_10_count']}")

    print("\nWrote:")
    print(f"  Latest CSV: {latest_path}")
    print(f"  Timestamped CSV: {snapshot_path}")
    print(f"  Append-only CSV: {append_path}")
    print(f"  Health JSON: {health_path}")
    print(f"  Latest health JSON: {latest_health_path}")

    if status_counts.get("OK", 0) == 0:
        raise SystemExit("No OK order books returned. Check token IDs / CLOB endpoint.")


if __name__ == "__main__":
    main()
