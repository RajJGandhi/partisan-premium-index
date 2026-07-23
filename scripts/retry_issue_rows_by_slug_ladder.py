#!/usr/bin/env python3
"""
scripts/retry_issue_rows_by_slug_ladder.py

Fixes v7 unresolved/wrong rows by trying obvious Polymarket parent/event slugs.

Why this exists:
- Full text search was bad.
- Full auto-accept was too broad.
- v7 topic gates were good, but some parent_slug_search_hint values were too naive.
- This script tries a ladder of likely event slugs for each remaining problematic parent.

Input:
    data/tracked_markets_resolved_v7.csv

Output:
    data/tracked_markets_resolved_v8.csv

Run:
    pip install requests

    python scripts/retry_issue_rows_by_slug_ladder.py \
      --input data/tracked_markets_resolved_v7.csv \
      --output data/tracked_markets_resolved_v8.csv

Optional:
    python scripts/retry_issue_rows_by_slug_ladder.py \
      --input data/tracked_markets_resolved_v7.csv \
      --output data/tracked_markets_resolved_v8.csv \
      --only-needs-review

Notes:
- No wallet/private key needed.
- This does NOT use score margin.
- It accepts the first executable candidate that passes parent-topic and outcome filters.
- If nothing passes, the row remains NEEDS_MANUAL_REVIEW or REPLACE.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

import requests


DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


RESOLUTION_FIELDS = [
    "gamma_market_id",
    "event_id",
    "condition_id",
    "description_text",
    "rules_text",
    "resolution_source",
    "end_date",
    "active",
    "closed",
    "resolved",
    "archived",
    "enable_order_book",
    "outcomes_json",
    "clob_token_ids_json",
    "outcome_token_map_json",
    "yes_token_id",
    "no_token_id",
    "volume",
    "liquidity",
    "market_url",
    "verification_status",
    "verified_at",
    "candidate_matches_json",
]

ADDED_COLUMNS = [
    "slug_ladder_tried_json",
    "slug_ladder_notes",
]


PARENT_SLUG_LADDERS: Dict[str, List[str]] = {
    # Seat-count exact rows; these may genuinely not exist, but try obvious exact slugs first.
    "Republican Senate seats after 2026 midterms": [
        "republican-senate-seats-after-the-2026-midterm-elections",
        "republican-senate-seats-after-2026-midterms",
        "republican-senate-seats-after-the-2026-midterms",
        "how-many-senate-seats-will-republicans-have-after-the-2026-midterm-elections",
        "how-many-senate-seats-will-the-republican-party-have-after-the-2026-midterm-elections",
        "republican-senate-seat-count-after-the-2026-midterm-elections",
    ],
    "Arizona Governor Election Winner": [
        "arizona-governor-election-winner",
        "arizona-governor-election-winner-2026",
        "2026-arizona-governor-election-winner",
        "who-will-win-the-arizona-governor-election",
        "who-will-win-the-2026-arizona-governor-election",
        "will-a-republican-win-the-arizona-governor-election",
        "will-a-democrat-win-the-arizona-governor-election",
        "arizona-governor-race-winner",
        "2026-arizona-governor-race-winner",
    ],
    "São Paulo Governor Election Winner": [
        "sao-paulo-governor-election-winner",
        "sao-paulo-governor-election-winner-2026",
        "2026-sao-paulo-governor-election-winner",
        "who-will-win-the-sao-paulo-governor-election",
        "who-will-win-the-2026-sao-paulo-governor-election",
        "sao-paulo-governor-race-winner",
        "brazil-sao-paulo-governor-election-winner",
        "sao-paulo-gubernatorial-election-winner",
    ],
    "Next Premier of Quebec": [
        "next-premier-of-quebec",
        "next-premier-of-quebec-2026",
        "quebec-next-premier",
        "quebec-next-premier-2026",
        "who-will-be-the-next-premier-of-quebec",
        "who-will-be-the-next-premier-of-quebec-after-the-next-election",
        "quebec-premier-after-the-next-election",
        "next-quebec-premier",
    ],
    "Quebec General Election Winner": [
        "quebec-general-election-winner",
        "quebec-general-election-winner-2026",
        "2026-quebec-general-election-winner",
        "quebec-election-winner",
        "quebec-election-winner-2026",
        "who-will-win-the-quebec-general-election",
        "who-will-win-the-2026-quebec-general-election",
        "quebec-provincial-election-winner",
        "2026-quebec-provincial-election-winner",
    ],
    "Toronto Mayoral Election Winner": [
        "toronto-mayoral-election-winner",
        "toronto-mayoral-election-winner-2026",
        "2026-toronto-mayoral-election-winner",
        "toronto-mayor-election-winner",
        "toronto-mayor-election-winner-2026",
        "2026-toronto-mayor-election-winner",
        "who-will-win-the-toronto-mayoral-election",
        "who-will-win-the-2026-toronto-mayoral-election",
        "who-will-be-elected-mayor-of-toronto",
        "who-will-be-the-next-mayor-of-toronto",
    ],
    "Next Prime Minister of New Zealand": [
        "next-prime-minister-of-new-zealand",
        "next-prime-minister-of-new-zealand-174",
        "next-prime-minister-of-new-zealand-2026",
        "new-zealand-next-prime-minister",
        "new-zealand-next-prime-minister-2026",
        "who-will-be-the-next-prime-minister-of-new-zealand",
        "who-will-be-the-next-prime-minister-of-new-zealand-after-the-next-election",
        "next-nz-prime-minister",
        "nz-next-prime-minister",
    ],
    "Brazil Presidential Election First Round: Margin of Victory": [
        "brazil-presidential-election-first-round-margin-of-victory",
        "brazil-presidential-election-first-round-margin-of-victory-2026",
        "2026-brazil-presidential-election-first-round-margin-of-victory",
        "brazil-election-first-round-margin-of-victory",
        "brazil-presidential-election-margin-of-victory",
        "brazil-presidential-election-first-round-winner-margin",
    ],
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def norm_ascii(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")


def normalize_text(value: Any) -> str:
    text = norm_ascii(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slugify(value: str) -> str:
    text = normalize_text(value)
    return re.sub(r"\s+", "-", text).strip("-")


def parse_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict, bool, int, float)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return value
    return value


def json_dumps(value: Any) -> str:
    if value is None or value == "":
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def first_present(data: Dict[str, Any], keys: Sequence[str], default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def truthy(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).lower()


def safe_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def request_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 2,
) -> Any:
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                time.sleep(1.0 * attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if attempt == retries:
                return None
            time.sleep(0.4 * attempt)
    return None


def flatten_gamma_response(data: Any, source: str = "") -> List[Dict[str, Any]]:
    if data is None:
        return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("markets"), list) and not data.get("question"):
            items = [data]
        elif isinstance(data.get("events"), list):
            items = data["events"]
        else:
            items = [data]
    else:
        return []

    markets: List[Dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        nested = item.get("markets")
        if isinstance(nested, list) and nested:
            event_id = first_present(item, ["id", "eventId"], "")
            event_slug = item.get("slug", "")
            event_title = first_present(item, ["title", "question", "name"], "")
            for m in nested:
                if not isinstance(m, dict):
                    continue
                m2 = dict(m)
                m2["_event_id"] = event_id
                m2["_event_slug"] = event_slug
                m2["_event_title"] = event_title
                m2["_source"] = source
                markets.append(m2)
            continue

        if item.get("question") or item.get("conditionId") or item.get("clobTokenIds"):
            m2 = dict(item)
            m2["_source"] = source
            markets.append(m2)

    return markets


def dedupe_markets(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for m in markets:
        key = str(first_present(m, ["id", "marketId", "conditionId", "slug", "question"], ""))
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def fetch_by_slug_all(session: requests.Session, gamma_base_url: str, slug: str) -> List[Dict[str, Any]]:
    slug = str(slug).strip().strip("/")
    if not slug:
        return []

    base = gamma_base_url.rstrip("/")
    calls = [
        ("events_slug_path", f"{base}/events/slug/{slug}", None),
        ("events_slug_query", f"{base}/events", {"slug": slug}),
        ("markets_slug_path", f"{base}/markets/slug/{slug}", None),
        ("markets_slug_query", f"{base}/markets", {"slug": slug}),
    ]

    candidates: List[Dict[str, Any]] = []
    for source, url, params in calls:
        data = request_json(session, url, params=params)
        candidates.extend(flatten_gamma_response(data, source=source))
    return dedupe_markets(candidates)


def fetch_by_text_search(session: requests.Session, gamma_base_url: str, query: str) -> List[Dict[str, Any]]:
    base = gamma_base_url.rstrip("/")
    params_list = [
        {"search": query, "active": "true", "closed": "false", "limit": 50},
        {"q": query, "active": "true", "closed": "false", "limit": 50},
        {"query": query, "active": "true", "closed": "false", "limit": 50},
    ]

    candidates: List[Dict[str, Any]] = []
    for endpoint in ["events", "markets"]:
        for params in params_list:
            data = request_json(session, f"{base}/{endpoint}", params=params)
            candidates.extend(flatten_gamma_response(data, source=f"{endpoint}_text_{next(iter(params.keys()))}"))

    return dedupe_markets(candidates)


def candidate_is_executable(market: Dict[str, Any]) -> bool:
    outcomes = parse_jsonish(first_present(market, ["outcomes"], ""))
    tokens = parse_jsonish(first_present(market, ["clobTokenIds", "clob_token_ids"], ""))

    if market.get("active") is not True:
        return False
    if market.get("closed") is True:
        return False
    if first_present(market, ["resolved"], False) is True:
        return False
    if first_present(market, ["archived"], False) is True:
        return False
    if first_present(market, ["enableOrderBook", "enable_order_book"], False) is not True:
        return False
    if not isinstance(outcomes, list) or not isinstance(tokens, list):
        return False
    if len(tokens) != 2:
        return False
    return True


def candidate_haystack(market: Dict[str, Any]) -> str:
    pieces = [
        first_present(market, ["question", "title", "name"], ""),
        market.get("slug", ""),
        market.get("_event_title", ""),
        market.get("_event_slug", ""),
    ]
    return normalize_text(" ".join(str(p) for p in pieces if p))


def split_terms(value: str) -> List[str]:
    return [normalize_text(x) for x in str(value or "").split("|") if normalize_text(x)]


def passes_filters(row: Dict[str, str], market: Dict[str, Any]) -> Tuple[bool, str]:
    hay = candidate_haystack(market)

    topic_terms = split_terms(row.get("topic_filter_all", ""))
    outcome_terms = split_terms(row.get("outcome_filter_any", ""))

    missing_topic = [t for t in topic_terms if t not in hay]
    if missing_topic:
        return False, f"missing_topic={missing_topic}"

    if outcome_terms and not any(t in hay for t in outcome_terms):
        return False, f"missing_outcome={outcome_terms}"

    return True, "passed"


def build_outcome_token_map(outcomes: Any, token_ids: Any) -> Dict[str, str]:
    parsed_outcomes = parse_jsonish(outcomes)
    parsed_tokens = parse_jsonish(token_ids)
    if not isinstance(parsed_outcomes, list) or not isinstance(parsed_tokens, list):
        return {}
    if len(parsed_outcomes) != len(parsed_tokens):
        return {}
    return {str(o): str(t) for o, t in zip(parsed_outcomes, parsed_tokens)}


def extract_event_id(market: Dict[str, Any]) -> str:
    return str(first_present(market, ["eventId", "event_id", "_event_id"], ""))


def enrich_row(row: Dict[str, str], market: Dict[str, Any], note: str) -> Dict[str, str]:
    outcomes = first_present(market, ["outcomes"], "")
    tokens = first_present(market, ["clobTokenIds", "clob_token_ids"], "")
    parsed_tokens = parse_jsonish(tokens)

    yes_token_id = ""
    no_token_id = ""
    if isinstance(parsed_tokens, list) and len(parsed_tokens) == 2:
        yes_token_id = str(parsed_tokens[0])
        no_token_id = str(parsed_tokens[1])

    slug = str(market.get("slug") or "")

    row.update(
        {
            "exact_polymarket_slug": slug,
            "gamma_market_id": str(first_present(market, ["id", "marketId", "market_id"], "")),
            "event_id": extract_event_id(market),
            "condition_id": str(first_present(market, ["conditionId", "condition_id"], "")),
            "question": str(first_present(market, ["question", "title", "name"], "")),
            "description_text": str(first_present(market, ["description"], "")),
            "rules_text": str(
                first_present(
                    market, ["rules", "marketRules", "resolutionRules", "additionalContext", "additional_context"], ""
                )
            ),
            "resolution_source": str(first_present(market, ["resolutionSource", "resolution_source"], "")),
            "end_date": str(first_present(market, ["endDate", "end_date"], "")),
            "active": truthy(market.get("active")),
            "closed": truthy(market.get("closed")),
            "resolved": truthy(first_present(market, ["resolved"], "")),
            "archived": truthy(first_present(market, ["archived"], "")),
            "enable_order_book": truthy(first_present(market, ["enableOrderBook", "enable_order_book"], "")),
            "outcomes_json": json_dumps(parse_jsonish(outcomes)),
            "clob_token_ids_json": json_dumps(parse_jsonish(tokens)),
            "outcome_token_map_json": json_dumps(build_outcome_token_map(outcomes, tokens)),
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "volume": str(first_present(market, ["volume", "volumeNum"], "")),
            "liquidity": str(first_present(market, ["liquidity", "liquidityNum"], "")),
            "market_url": f"https://polymarket.com/event/{slug}" if slug else "",
            "verification_status": "VERIFIED_READY",
            "verified_at": now_iso(),
            "resolver_notes": note,
            "status": "resolved_by_slug_ladder",
        }
    )
    return row


def candidate_score(row: Dict[str, str], market: Dict[str, Any]) -> float:
    """
    Simple deterministic ranking:
    - executable first
    - row filters pass
    - volume/liquidity boost
    """
    if not candidate_is_executable(market):
        return -999
    ok, _ = passes_filters(row, market)
    if not ok:
        return -500

    volume = safe_float(first_present(market, ["volume", "volumeNum"], 0))
    liquidity = safe_float(first_present(market, ["liquidity", "liquidityNum"], 0))
    hay = candidate_haystack(market)
    outcome_terms = split_terms(row.get("outcome_filter_any", ""))

    exactish = 1.0 if outcome_terms and any(t in hay for t in outcome_terms) else 0
    return 100 + exactish + min(10, (volume + liquidity) / 1_000_000)


def should_retry(row: Dict[str, str], only_needs_review: bool) -> bool:
    if only_needs_review:
        return row.get("verification_status", "") in {
            "NEEDS_MANUAL_REVIEW",
            "NOT_EXECUTABLE_NEEDS_REVIEW",
            "REPLACE",
            "",
        }
    return row.get("parent_market_name", "") in PARENT_SLUG_LADDERS


def candidate_slugs_for_row(row: Dict[str, str]) -> List[str]:
    parent = row.get("parent_market_name", "")
    outcome = row.get("primary_outcome_to_track", "")
    question = row.get("search_query") or row.get("question") or ""

    slugs = []

    # Try exact option slug first, then parent ladder.
    if question:
        slugs.append(slugify(question))
    if row.get("slug_search_hint"):
        slugs.append(row["slug_search_hint"])
    if row.get("exact_polymarket_slug") and row["exact_polymarket_slug"] not in {"TODO_VERIFY", "TODO"}:
        slugs.append(row["exact_polymarket_slug"])

    slugs.extend(PARENT_SLUG_LADDERS.get(parent, []))

    # Special option-level exact bracket attempts.
    if parent == "Republican Senate seats after 2026 midterms":
        if outcome in {"48", "49", "50", "51", "52"}:
            slugs.insert(
                0, f"will-the-republican-party-hold-exactly-{outcome}-senate-seats-after-the-2026-midterm-elections"
            )

    # Preserve current parent hint, but not first, because it was often too naive.
    if row.get("parent_slug_search_hint"):
        slugs.append(row["parent_slug_search_hint"])

    # De-dupe.
    out = []
    seen = set()
    for s in slugs:
        s = str(s or "").strip().strip("/")
        if not s or s in seen or s in {"TODO_VERIFY", "TODO"}:
            continue
        seen.add(s)
        out.append(s)
    return out


def serialize_candidates(candidates: List[Dict[str, Any]]) -> str:
    packed = []
    for m in candidates[:20]:
        packed.append(
            {
                "id": first_present(m, ["id", "marketId", "market_id"], ""),
                "slug": m.get("slug", ""),
                "question": first_present(m, ["question", "title", "name"], ""),
                "event_title": m.get("_event_title", ""),
                "source": m.get("_source", ""),
                "active": m.get("active", ""),
                "closed": m.get("closed", ""),
                "enableOrderBook": first_present(m, ["enableOrderBook", "enable_order_book"], ""),
                "volume": first_present(m, ["volume", "volumeNum"], ""),
                "liquidity": first_present(m, ["liquidity", "liquidityNum"], ""),
            }
        )
    return json_dumps(packed)


def read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    for col in ADDED_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        for col in fieldnames:
            row.setdefault(col, "")

    return rows, fieldnames


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Retry v7 issue rows using obvious Polymarket slug ladders.")
    p.add_argument("--input", default="data/tracked_markets_resolved_v7.csv")
    p.add_argument("--output", default="data/tracked_markets_resolved_v8.csv")
    p.add_argument("--gamma-base-url", default=DEFAULT_GAMMA_BASE_URL)
    p.add_argument("--only-needs-review", action="store_true", help="Retry only unresolved/replace rows.")
    p.add_argument("--include-text-search", action="store_true", help="Also try text search after slug ladder.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    rows, fieldnames = read_csv(Path(args.input))
    session = requests.Session()

    total_retried = 0
    resolved = 0
    unresolved = 0

    for row in rows:
        if not should_retry(row, only_needs_review=args.only_needs_review):
            continue

        parent = row.get("parent_market_name", "")
        slugs = candidate_slugs_for_row(row)
        all_candidates: List[Dict[str, Any]] = []

        for slug in slugs:
            all_candidates.extend(fetch_by_slug_all(session, args.gamma_base_url, slug))

        if args.include_text_search:
            query = row.get("search_query") or row.get("question") or row.get("market_name") or ""
            if query:
                all_candidates.extend(fetch_by_text_search(session, args.gamma_base_url, query))

        all_candidates = dedupe_markets(all_candidates)
        row["slug_ladder_tried_json"] = json_dumps(slugs)
        row["candidate_matches_json"] = serialize_candidates(all_candidates)

        ranked = sorted(all_candidates, key=lambda m: candidate_score(row, m), reverse=True)
        chosen: Optional[Dict[str, Any]] = None
        reject_reason = ""

        for cand in ranked:
            if not candidate_is_executable(cand):
                continue
            ok, reason = passes_filters(row, cand)
            if not ok:
                reject_reason = reason
                continue
            chosen = cand
            break

        total_retried += 1

        if chosen:
            note = f"SLUG_LADDER_ACCEPTED parent={parent}; tried={len(slugs)} slugs; candidate_source={chosen.get('_source', '')}"
            enrich_row(row, chosen, note)
            row["slug_ladder_notes"] = note
            resolved += 1
            print(
                f"RESOLVED {row.get('tracking_id')} {parent} — {row.get('primary_outcome_to_track')} -> {chosen.get('slug')}"
            )
        else:
            if row.get("verification_status") == "REPLACE":
                # keep REPLACE
                row["slug_ladder_notes"] = (
                    f"Still REPLACE after slug ladder; candidates={len(all_candidates)}; last_reject={reject_reason}"
                )
            else:
                row["verification_status"] = "NEEDS_MANUAL_REVIEW"
                row["slug_ladder_notes"] = (
                    f"No executable candidate passed filters after slug ladder; candidates={len(all_candidates)}; last_reject={reject_reason}"
                )
            unresolved += 1
            print(
                f"UNRESOLVED {row.get('tracking_id')} {parent} — {row.get('primary_outcome_to_track')} | candidates={len(all_candidates)}"
            )

    write_csv(Path(args.output), rows, fieldnames)

    print("\nDone.")
    print(f"Retried rows: {total_retried}")
    print(f"Resolved rows: {resolved}")
    print(f"Still unresolved/replace: {unresolved}")
    print(f"Wrote: {args.output}")


if __name__ == "__main__":
    main()
