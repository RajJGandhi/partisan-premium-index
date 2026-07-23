#!/usr/bin/env python3
"""
scripts/resolve_market_ids.py

Reality Spread semi-manual Polymarket Gamma resolver, v7.

Fixes from v1:
- Does NOT depend on fetching the first 100 active markets, which can be mostly World Cup/trending markets.
- Searches Gamma per row using repaired parent/option slugs first, then /markets and /events query params.
- Demotes inactive/archived/no-CLOB candidates so dead archived rows are not marked ready.
- Adds full auto-accept mode with topic/outcome gates: accepts first executable match that passes row filters.
- Avoids the blocked /search endpoint; manual paste supports both market slugs and event slugs:
    /markets/slug/{slug}
    /events/slug/{slug}
    /markets?slug={slug}
    /events?slug={slug}
- If an event slug returns nested markets, it extracts those markets as candidates.
- Keeps user-in-the-loop: top candidates shown, user chooses correct match.
- Writes progress after every row.

Install:
    pip install requests

Run:
    python scripts/resolve_market_ids.py \
      --input data/tracked_markets.csv \
      --output data/tracked_markets_resolved.csv

Safe run:
    python scripts/resolve_market_ids.py --limit-rows 5

Full auto-accept run:
    python scripts/resolve_market_ids.py \
      --input data/tracked_markets_resolved_repaired.csv \
      --output data/tracked_markets_resolved_v6.csv \
      --auto-accept

Fully non-interactive full auto-accept run:
    python scripts/resolve_market_ids.py \
      --input data/tracked_markets_resolved_repaired.csv \
      --output data/tracked_markets_resolved_v6.csv \
      --auto-accept \
      --non-interactive
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

import requests


DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

ENRICHED_COLUMNS = [
    "exact_polymarket_slug",
    "gamma_market_id",
    "event_id",
    "condition_id",
    "question",
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
    "resolver_notes",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_set(value: str) -> set[str]:
    return {t for t in normalize_text(value).split() if len(t) > 1}


def first_present(data: Dict[str, Any], keys: Sequence[str], default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


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


def json_dumps_compact(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def truthy(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value).lower()


def safe_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return 0.0


def read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    if not rows:
        raise ValueError(f"No rows found in {path}")

    for col in ENRICHED_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    for row in rows:
        for col in fieldnames:
            row.setdefault(col, "")

    return rows, fieldnames


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.backup_{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def request_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 2,
    sleep_seconds: float = 0.4,
) -> Any:
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                time.sleep(sleep_seconds * attempt * 2)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)
            else:
                print(f"  WARN: GET failed: {url} params={params} error={last_error}")
                return None
    return None


def extract_slug(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""

    if "polymarket.com" not in raw:
        return raw.strip("/")

    parsed = urlparse(raw)
    parts = [unquote(p) for p in parsed.path.split("/") if p]
    if not parts:
        return ""

    # Common:
    # /event/<event_slug>
    # /event/<event_slug>/<market_slug>
    # Usually the first segment after /event is the event slug.
    if "event" in parts:
        idx = parts.index("event")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    return parts[-1]


def flatten_gamma_response(data: Any, source: str = "") -> List[Dict[str, Any]]:
    """
    Convert a Gamma response into market-like dicts.

    /markets returns list[market] or market.
    /events returns list[event] or event; event may include nested markets.
    """
    if data is None:
        return []

    if isinstance(data, list):
        items: List[Any] = data
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
                if event_id and not m2.get("_event_id"):
                    m2["_event_id"] = event_id
                if event_slug and not m2.get("_event_slug"):
                    m2["_event_slug"] = event_slug
                if event_title and not m2.get("_event_title"):
                    m2["_event_title"] = event_title
                if source:
                    m2["_source"] = source
                markets.append(m2)
            continue

        if item.get("question") or item.get("conditionId") or item.get("clobTokenIds"):
            m2 = dict(item)
            if source:
                m2["_source"] = source
            markets.append(m2)

    return markets


def dedupe_markets(markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for m in markets:
        key = str(first_present(m, ["id", "marketId", "conditionId", "slug", "question"], ""))
        if not key:
            key = json_dumps_compact(m)[:300]
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def row_search_text(row: Dict[str, str]) -> str:
    # Prefer exact user-facing labels/questions. Avoid selected_reason/notes; those pollute matching.
    for col in ["search_query", "question", "market_name", "name"]:
        val = str(row.get(col, "") or "").strip()
        if val and val.upper() not in {"TODO", "TODO_VERIFY", "NAN"}:
            return val

    for col in ["selected_reason", "notes"]:
        val = str(row.get(col, "") or "").strip()
        if val:
            return val

    return ""


def compact_query(query: str) -> str:
    q = query.strip()
    if "?" in q:
        before = q.split("?", 1)[0] + "?"
        if len(before) > 140 and "Which " in before:
            before = "Which " + before.split("Which ", 1)[1]
        return before.strip()
    return q[:120].strip()


def candidate_is_executable(market: Dict[str, Any]) -> bool:
    """
    A Reality Spread row is only usable if it has an executable order book.
    This prevents archived/placeholder/zero-CLOB matches from becoming VERIFIED_READY.
    """
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


def row_has_executable_tokens(row: Dict[str, str]) -> bool:
    tokens = parse_jsonish(row.get("clob_token_ids_json", ""))
    if str(row.get("verification_status", "")).strip().upper() != "VERIFIED_READY":
        return False
    if str(row.get("active", "")).strip().lower() != "true":
        return False
    if str(row.get("closed", "")).strip().lower() == "true":
        return False
    if str(row.get("resolved", "")).strip().lower() == "true":
        return False
    if str(row.get("archived", "")).strip().lower() == "true":
        return False
    if str(row.get("enable_order_book", "")).strip().lower() != "true":
        return False
    if not isinstance(tokens, list) or len(tokens) != 2:
        return False
    if not str(row.get("yes_token_id", "")).strip() or not str(row.get("no_token_id", "")).strip():
        return False
    return True


def party_signal_boost(q_norm: str, hay_norm: str) -> float:
    """
    Fixes rows where the parent event is right but the wrong party/candidate ranks first.
    Example: query asks Republicans, but Democrat outcome had slightly better fuzzy score.
    """
    boost = 0.0

    q_dem = any(t in q_norm.split() for t in ["democrat", "democrats", "democratic"])
    q_rep = any(t in q_norm.split() for t in ["republican", "republicans", "gop"])
    q_ind = "independent" in q_norm.split()

    h_dem = any(t in hay_norm.split() for t in ["democrat", "democrats", "democratic"])
    h_rep = any(t in hay_norm.split() for t in ["republican", "republicans", "gop"])
    h_ind = "independent" in hay_norm.split()

    if q_dem and h_dem:
        boost += 0.18
    if q_rep and h_rep:
        boost += 0.18
    if q_ind and h_ind:
        boost += 0.18

    if q_dem and h_rep:
        boost -= 0.12
    if q_rep and h_dem:
        boost -= 0.12

    return boost


def candidate_score(query: str, market: Dict[str, Any]) -> float:
    q_norm = normalize_text(query)
    question = str(first_present(market, ["question", "title", "name"], ""))
    slug = str(market.get("slug") or "")
    event_title = str(market.get("_event_title") or "")
    source = str(market.get("_source") or "")

    question_norm = normalize_text(question)
    slug_norm = normalize_text(slug.replace("-", " "))
    event_norm = normalize_text(event_title)
    hay_norm = question_norm + " " + slug_norm + " " + event_norm

    seq_question = difflib.SequenceMatcher(None, q_norm, question_norm).ratio()
    seq_slug = difflib.SequenceMatcher(None, q_norm, slug_norm).ratio()
    seq_event = difflib.SequenceMatcher(None, q_norm, event_norm).ratio() if event_norm else 0

    q_tokens = token_set(q_norm)
    hay_tokens = token_set(hay_norm)
    overlap = len(q_tokens & hay_tokens) / max(1, len(q_tokens))

    volume = safe_float(first_present(market, ["volume", "volumeNum"], 0))
    liquidity = safe_float(first_present(market, ["liquidity", "liquidityNum"], 0))

    live_boost = 0.0
    if market.get("active") is True:
        live_boost += 0.05
    else:
        live_boost -= 0.35
    if market.get("closed") is False:
        live_boost += 0.04
    else:
        live_boost -= 0.20
    if first_present(market, ["clobTokenIds", "clob_token_ids"], ""):
        live_boost += 0.06
    else:
        live_boost -= 0.20
    if first_present(market, ["enableOrderBook", "enable_order_book"], "") is True:
        live_boost += 0.05
    else:
        live_boost -= 0.15
    if first_present(market, ["archived"], False) is True or first_present(market, ["resolved"], False) is True:
        live_boost -= 0.25

    source_boost = 0.0
    if source in {"events_slug_path", "markets_slug_path", "events_slug_query", "markets_slug_query"}:
        source_boost += 0.16

    volume_boost = min(0.05, (volume + liquidity) / 10_000_000)
    side_boost = party_signal_boost(q_norm, hay_norm)
    executable_boost = 0.08 if candidate_is_executable(market) else -0.25

    return (
        0.40 * seq_question
        + 0.18 * seq_slug
        + 0.10 * seq_event
        + 0.18 * overlap
        + live_boost
        + source_boost
        + volume_boost
        + side_boost
        + executable_boost
    )


def fetch_by_slug_all(session: requests.Session, gamma_base_url: str, slug_or_url: str) -> List[Dict[str, Any]]:
    slug = extract_slug(slug_or_url)
    if not slug:
        return []

    base = gamma_base_url.rstrip("/")
    candidates: List[Dict[str, Any]] = []

    calls = [
        ("markets_slug_path", f"{base}/markets/slug/{slug}", None),
        ("events_slug_path", f"{base}/events/slug/{slug}", None),
        ("markets_slug_query", f"{base}/markets", {"slug": slug}),
        ("events_slug_query", f"{base}/events", {"slug": slug}),
    ]

    for source, url, params in calls:
        data = request_json(session, url, params=params)
        candidates.extend(flatten_gamma_response(data, source=source))

    return dedupe_markets(candidates)


def useful_slug(value: str) -> bool:
    value = str(value or "").strip()
    return bool(value) and value.upper() not in {"TODO", "TODO_VERIFY", "NAN", "NONE", "NULL"}


def row_slug_hints(row: Dict[str, str], query: str) -> List[str]:
    """
    Return slug/url hints in the order we should try them.

    Key fix:
    For option-level rows like "Maine Senate Election Winner — Democrat",
    the row's exact option query often searches badly. The parent event slug
    like "maine-senate-election-winner" is much more reliable because Gamma
    can return all nested outcomes for the event.
    """
    hints: List[str] = []

    # Highest leverage: parent event slug. This solves state Senate / governor / multi-outcome rows.
    for col in ["parent_slug_search_hint", "exact_polymarket_slug", "slug_search_hint", "market_url"]:
        value = str(row.get(col, "") or "").strip()
        if useful_slug(value):
            hints.append(value)

    # Slug generated from the clean query as a fallback.
    q = compact_query(query)
    slug_guess = re.sub(r"[^a-z0-9]+", "-", normalize_text(q)).strip("-")
    if slug_guess:
        hints.append(slug_guess)

    # Preserve order, remove duplicates.
    out: List[str] = []
    seen: set[str] = set()
    for h in hints:
        h = h.strip()
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def search_gamma_for_row(
    session: requests.Session,
    gamma_base_url: str,
    query: str,
    row: Optional[Dict[str, str]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Search Gamma in a slug-first way.

    v3 change:
    1. Try parent_slug_search_hint and slug_search_hint first.
    2. Then try /markets and /events search.
    3. Do NOT call /search because it returns 401 on current Gamma.
    """
    base = gamma_base_url.rstrip("/")
    q = compact_query(query)
    candidates: List[Dict[str, Any]] = []

    if row is not None:
        for hint in row_slug_hints(row, query):
            candidates.extend(fetch_by_slug_all(session, gamma_base_url, hint))

    query_params = [
        {"search": q, "active": "true", "closed": "false", "limit": limit},
        {"q": q, "active": "true", "closed": "false", "limit": limit},
        {"query": q, "active": "true", "closed": "false", "limit": limit},
    ]

    for endpoint in ["markets", "events"]:
        for params in query_params:
            data = request_json(session, f"{base}/{endpoint}", params=params)
            candidates.extend(flatten_gamma_response(data, source=f"/{endpoint}?{list(params.keys())[0]}"))

    candidates = dedupe_markets(candidates)
    candidates.sort(key=lambda m: candidate_score(q, m), reverse=True)
    return candidates


def top_candidates(query: str, markets: List[Dict[str, Any]], n: int = 5) -> List[Tuple[float, Dict[str, Any]]]:
    q = compact_query(query)
    scored = [(candidate_score(q, m), m) for m in markets]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


def market_summary(market: Dict[str, Any]) -> str:
    question = str(first_present(market, ["question", "title", "name"], ""))
    slug = str(market.get("slug") or "")
    mid = str(first_present(market, ["id", "marketId", "market_id"], ""))
    end_date = str(first_present(market, ["endDate", "end_date"], ""))
    volume = str(first_present(market, ["volume", "volumeNum"], ""))
    liquidity = str(first_present(market, ["liquidity", "liquidityNum"], ""))
    active = truthy(market.get("active"))
    closed = truthy(market.get("closed"))
    source = str(market.get("_source") or "")
    event_title = str(market.get("_event_title") or "")
    return (
        f"question={question}\n"
        f"event_title={event_title}\n"
        f"slug={slug}\n"
        f"id={mid} | end={end_date} | active={active} | closed={closed} | "
        f"volume={volume} | liquidity={liquidity} | source={source} | "
        f"executable={candidate_is_executable(market)}"
    )


def print_candidates(candidates: List[Tuple[float, Dict[str, Any]]]) -> None:
    if not candidates:
        print("No candidates found.")
        return

    for i, (score, market) in enumerate(candidates, start=1):
        question = str(first_present(market, ["question", "title", "name"], ""))[:150]
        slug = str(market.get("slug") or "")[:100]
        event_title = str(market.get("_event_title") or "")[:100]
        mid = str(first_present(market, ["id", "marketId", "market_id"], ""))
        end_date = str(first_present(market, ["endDate", "end_date"], ""))
        volume = str(first_present(market, ["volume", "volumeNum"], ""))
        active = truthy(market.get("active"))
        closed = truthy(market.get("closed"))
        source = str(market.get("_source") or "")
        ready = "READY" if candidate_is_executable(market) else "NOT_READY"
        print(
            f"\n[{i}] score={score:.3f} {ready} id={mid} active={active} closed={closed} "
            f"end={end_date} volume={volume} source={source}\n"
            f"    Event: {event_title}\n"
            f"    Q: {question}\n"
            f"    slug: {slug}"
        )


def extract_event_id(market: Dict[str, Any]) -> str:
    direct = first_present(market, ["eventId", "event_id", "_event_id"], "")
    if direct:
        return str(direct)

    events = market.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            return str(first_present(first, ["id", "eventId"], ""))
    if isinstance(events, dict):
        return str(first_present(events, ["id", "eventId"], ""))

    return ""


def build_outcome_token_map(outcomes: Any, token_ids: Any) -> Dict[str, str]:
    parsed_outcomes = parse_jsonish(outcomes)
    parsed_tokens = parse_jsonish(token_ids)

    if not isinstance(parsed_outcomes, list) or not isinstance(parsed_tokens, list):
        return {}

    if len(parsed_outcomes) != len(parsed_tokens):
        return {}

    return {str(outcome): str(token) for outcome, token in zip(parsed_outcomes, parsed_tokens)}


def enrich_row_from_market(
    row: Dict[str, str], market: Dict[str, Any], status: str = "VERIFIED_READY"
) -> Dict[str, str]:
    if status == "VERIFIED_READY" and not candidate_is_executable(market):
        status = "NOT_EXECUTABLE_NEEDS_REVIEW"

    slug = str(market.get("slug") or "")
    market_id = str(first_present(market, ["id", "marketId", "market_id"], ""))
    condition_id = str(first_present(market, ["conditionId", "condition_id"], ""))
    question = str(first_present(market, ["question", "title", "name"], ""))

    outcomes = first_present(market, ["outcomes"], "")
    clob_token_ids = first_present(market, ["clobTokenIds", "clob_token_ids"], "")
    outcome_token_map = build_outcome_token_map(outcomes, clob_token_ids)

    parsed_tokens = parse_jsonish(clob_token_ids)
    yes_token_id = ""
    no_token_id = ""
    if isinstance(parsed_tokens, list) and len(parsed_tokens) == 2:
        yes_token_id = str(parsed_tokens[0])
        no_token_id = str(parsed_tokens[1])

    row.update(
        {
            "exact_polymarket_slug": slug,
            "gamma_market_id": market_id,
            "event_id": extract_event_id(market),
            "condition_id": condition_id,
            "question": question,
            "description_text": str(first_present(market, ["description"], "")),
            "rules_text": str(
                first_present(
                    market,
                    ["rules", "marketRules", "resolutionRules", "additionalContext", "additional_context"],
                    "",
                )
            ),
            "resolution_source": str(first_present(market, ["resolutionSource", "resolution_source"], "")),
            "end_date": str(first_present(market, ["endDate", "end_date"], "")),
            "active": truthy(market.get("active")),
            "closed": truthy(market.get("closed")),
            "resolved": truthy(first_present(market, ["resolved"], "")),
            "archived": truthy(first_present(market, ["archived"], "")),
            "enable_order_book": truthy(first_present(market, ["enableOrderBook", "enable_order_book"], "")),
            "outcomes_json": json_dumps_compact(parse_jsonish(outcomes)),
            "clob_token_ids_json": json_dumps_compact(parse_jsonish(clob_token_ids)),
            "outcome_token_map_json": json_dumps_compact(outcome_token_map),
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "volume": str(first_present(market, ["volume", "volumeNum"], "")),
            "liquidity": str(first_present(market, ["liquidity", "liquidityNum"], "")),
            "market_url": f"https://polymarket.com/event/{slug}" if slug else "",
            "verification_status": status,
            "verified_at": now_iso(),
        }
    )
    return row


def serialize_candidate_matches(candidates: List[Tuple[float, Dict[str, Any]]]) -> str:
    packed = []
    for score, m in candidates:
        packed.append(
            {
                "score": round(score, 4),
                "id": first_present(m, ["id", "marketId", "market_id"], ""),
                "slug": m.get("slug", ""),
                "question": first_present(m, ["question", "title", "name"], ""),
                "event_title": m.get("_event_title", ""),
                "endDate": first_present(m, ["endDate", "end_date"], ""),
                "volume": first_present(m, ["volume", "volumeNum"], ""),
                "source": m.get("_source", ""),
                "active": m.get("active", ""),
                "closed": m.get("closed", ""),
            }
        )
    return json_dumps_compact(packed)


def is_already_verified(row: Dict[str, str]) -> bool:
    # Do not skip rows that were previously "verified" to inactive/no-CLOB markets.
    return row_has_executable_tokens(row)


def prompt_choice() -> str:
    print("\nChoose: [1-5]=accept candidate | m=manual slug/url | s=skip | r=replace | n=needs review | q=quit/save")
    return input("> ").strip()


def choose_manual_market(session: requests.Session, gamma_base_url: str) -> Optional[Dict[str, Any]]:
    slug = input("Paste Polymarket slug or full /event/ URL: ").strip()
    candidates = fetch_by_slug_all(session, gamma_base_url, slug)

    if not candidates:
        print("Could not fetch any market/event by that slug/url.")
        print("Try using only the slug after /event/, or mark row as needs review.")
        return None

    print(f"\nFetched {len(candidates)} candidate(s) from slug/url:")
    scored = top_candidates(slug, candidates, n=min(10, len(candidates)))
    print_candidates(scored)

    if len(scored) == 1:
        confirm = input("Accept this match? [y/N] ").strip().lower()
        return scored[0][1] if confirm == "y" else None

    while True:
        pick = input("Choose candidate number, or blank to cancel: ").strip()
        if not pick:
            return None
        if pick.isdigit() and 1 <= int(pick) <= len(scored):
            market = scored[int(pick) - 1][1]
            print("\nSelected manual candidate:")
            print(market_summary(market))
            confirm = input("Accept this match? [y/N] ").strip().lower()
            return market if confirm == "y" else None
        print("Invalid choice.")


def normalize_for_gate(value: Any) -> str:
    text = normalize_text(value)
    # normalize_text already strips punctuation; keep this as semantic wrapper.
    return text


def split_filter_terms(value: str) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    return [normalize_for_gate(x) for x in raw.split("|") if normalize_for_gate(x)]


def candidate_haystack(market: Dict[str, Any]) -> str:
    pieces = [
        first_present(market, ["question", "title", "name"], ""),
        market.get("slug", ""),
        market.get("_event_title", ""),
        market.get("_event_slug", ""),
    ]
    return normalize_for_gate(" ".join(str(p) for p in pieces if p))


def candidate_passes_row_filters(row: Optional[Dict[str, str]], market: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Row-level hard gates.

    topic_filter_all:
      pipe-separated terms; every term must appear somewhere in candidate
      question/slug/event title.

    outcome_filter_any:
      pipe-separated terms; at least one term must appear somewhere in candidate
      question/slug/event title.

    This keeps full auto-accept aggressive while preventing nonsense matches like
    Toronto Mayor -> 2028 US President or Arizona Governor -> Senate control.
    """
    if row is None:
        return True, "no_row_filters"

    hay = candidate_haystack(market)
    required_all = split_filter_terms(row.get("topic_filter_all", ""))
    required_any = split_filter_terms(row.get("outcome_filter_any", ""))

    missing = [term for term in required_all if term not in hay]
    if missing:
        return False, f"missing_topic_terms={missing}"

    if required_any and not any(term in hay for term in required_any):
        return False, f"missing_outcome_terms={required_any}"

    return True, "passed_row_filters"


def auto_accept_candidate(
    candidates: List[Tuple[float, Dict[str, Any]]],
    min_score: float = 0.0,
    min_margin: float = 0.0,
    require_executable: bool = True,
    row: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[float], Optional[Dict[str, Any]], str]:
    """
    Full auto-accept with topic/outcome gates.

    Behavior:
    - Ignores score threshold and margin.
    - Walks ranked candidates in order.
    - Accepts the first executable candidate that passes row topic/outcome filters.
    - If no candidate passes, returns no match.

    This keeps throughput high while avoiding wrong-parent garbage.
    """
    if not candidates:
        return None, None, "no_candidates"

    rejection_samples: List[str] = []

    for rank, (score, market) in enumerate(candidates, start=1):
        if require_executable and not candidate_is_executable(market):
            if len(rejection_samples) < 5:
                rejection_samples.append(f"rank={rank}:not_executable")
            continue

        ok, reason = candidate_passes_row_filters(row, market)
        if not ok:
            if len(rejection_samples) < 5:
                q = str(first_present(market, ["question", "title", "name"], ""))[:90]
                rejection_samples.append(f"rank={rank}:{reason}:q={q}")
            continue

        top_score = candidates[0][0]
        second_score = candidates[1][0] if len(candidates) > 1 else -999.0
        return (
            score,
            market,
            f"accepted rank={rank} score={score:.4f} top={top_score:.4f} second={second_score:.4f}; margin disabled; row filters passed",
        )

    return None, None, "no_executable_candidate_passed_filters; " + "; ".join(rejection_samples)


def resolve_rows(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    output_path: Path,
    gamma_base_url: str,
    force: bool = False,
    limit_rows: Optional[int] = None,
    top_n: int = 5,
    auto_accept: bool = False,
    auto_min_score: float = 0.86,
    auto_min_margin: float = 0.08,
    non_interactive: bool = False,
) -> None:
    processed = 0
    session = requests.Session()

    for idx, row in enumerate(rows):
        if limit_rows is not None and processed >= limit_rows:
            print(f"\nReached --limit-rows={limit_rows}. Saving and exiting.")
            break

        if is_already_verified(row) and not force:
            continue

        market_num = row.get("market_num") or row.get("id") or str(idx + 1)
        query = row_search_text(row)
        if not query:
            row["verification_status"] = "NEEDS_MANUAL_REVIEW"
            row["resolver_notes"] = "No usable market_name/search_query/question text found."
            write_csv(output_path, rows, fieldnames)
            continue

        print("\n" + "=" * 90)
        print(f"Row {idx + 1}/{len(rows)} | market_num={market_num}")
        print(f"Search text: {query}")
        print(f"Compact query: {compact_query(query)}")

        raw_candidates = search_gamma_for_row(session, gamma_base_url, query, row=row)
        candidates = top_candidates(query, raw_candidates, n=top_n)
        row["candidate_matches_json"] = serialize_candidate_matches(candidates)

        print_candidates(candidates)

        if auto_accept:
            auto_score, auto_market, auto_reason = auto_accept_candidate(
                candidates,
                min_score=auto_min_score,
                min_margin=auto_min_margin,
                require_executable=True,
                row=row,
            )
            if auto_market is not None and auto_score is not None:
                enrich_row_from_market(row, auto_market, "VERIFIED_READY")
                row["resolver_notes"] = f"AUTO_ACCEPTED: {auto_reason}"
                processed += 1
                write_csv(output_path, rows, fieldnames)
                print(f"AUTO_ACCEPTED row {idx + 1}: {row.get('market_name', '')} | {auto_reason}")
                print(f"Saved progress to {output_path}")
                continue

            print(f"AUTO_ACCEPT_SKIPPED: {auto_reason}")

            if non_interactive:
                row["verification_status"] = "NEEDS_MANUAL_REVIEW"
                row["resolver_notes"] = f"AUTO_ACCEPT_SKIPPED_NON_INTERACTIVE: {auto_reason}"
                processed += 1
                write_csv(output_path, rows, fieldnames)
                print(f"Marked row {idx + 1} NEEDS_MANUAL_REVIEW and saved progress to {output_path}")
                continue

        while True:
            choice = prompt_choice().lower()

            if choice == "q":
                write_csv(output_path, rows, fieldnames)
                print(f"Saved progress to {output_path}")
                sys.exit(0)

            if choice == "s":
                row["verification_status"] = "SKIPPED"
                row["resolver_notes"] = "User skipped during manual resolution."
                break

            if choice == "r":
                row["verification_status"] = "REPLACE"
                row["resolver_notes"] = "User marked this market for replacement."
                break

            if choice == "n":
                row["verification_status"] = "NEEDS_MANUAL_REVIEW"
                row["resolver_notes"] = "User marked row as needing manual review."
                break

            if choice == "m":
                market = choose_manual_market(session, gamma_base_url)
                if market is None:
                    continue
                enrich_row_from_market(row, market, "VERIFIED_READY")
                row["resolver_notes"] = "Accepted via manual slug/url."
                break

            if choice.isdigit():
                pick = int(choice)
                if 1 <= pick <= len(candidates):
                    score, market = candidates[pick - 1]
                    print("\nSelected candidate:")
                    print(market_summary(market))
                    confirm = input("Accept this match? [y/N] ").strip().lower()
                    if confirm == "y":
                        enrich_row_from_market(row, market, "VERIFIED_READY")
                        row["resolver_notes"] = f"Accepted candidate rank {pick} with fuzzy_score={score:.4f}."
                        break
                    print("Not accepted. Choose again.")
                    continue

            print("Invalid choice. Try again.")

        processed += 1
        write_csv(output_path, rows, fieldnames)
        print(f"Saved progress to {output_path}")

    write_csv(output_path, rows, fieldnames)
    print("\nDone.")
    print(f"Output written to: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semi-manually resolve Reality Spread tracked markets to Gamma IDs/slugs."
    )
    parser.add_argument("--input", default="data/tracked_markets.csv", help="Input tracked markets CSV.")
    parser.add_argument("--output", default="data/tracked_markets_resolved.csv", help="Output CSV path.")
    parser.add_argument("--in-place", action="store_true", help="Write back to --input after timestamped backup.")
    parser.add_argument("--gamma-base-url", default=os.getenv("POLYMARKET_GAMMA_BASE_URL", DEFAULT_GAMMA_BASE_URL))
    parser.add_argument("--force", action="store_true", help="Re-resolve rows already marked VERIFIED_READY.")
    parser.add_argument("--limit-rows", type=int, default=None, help="Resolve only first N unresolved rows.")
    parser.add_argument("--top-n", type=int, default=5, help="Number of candidates shown per row.")
    parser.add_argument(
        "--auto-accept",
        action="store_true",
        help="Automatically accept the first executable match that passes row topic/outcome filters; ignores score and margin guardrails.",
    )
    parser.add_argument(
        "--auto-min-score",
        type=float,
        default=0.0,
        help="Deprecated in v6; ignored because auto-accept has no score threshold.",
    )
    parser.add_argument(
        "--auto-min-margin",
        type=float,
        default=0.0,
        help="Deprecated in v6; ignored because auto-accept has no margin threshold.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt when auto-accept fails; mark row NEEDS_MANUAL_REVIEW instead.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = input_path if args.in_place else Path(args.output)

    rows, fieldnames = read_csv(input_path)

    if args.in_place:
        backup = backup_file(input_path)
        if backup:
            print(f"Backup created: {backup}")

    resolve_rows(
        rows=rows,
        fieldnames=fieldnames,
        output_path=output_path,
        gamma_base_url=args.gamma_base_url,
        force=args.force,
        limit_rows=args.limit_rows,
        top_n=args.top_n,
        auto_accept=args.auto_accept,
        auto_min_score=args.auto_min_score,
        auto_min_margin=args.auto_min_margin,
        non_interactive=args.non_interactive,
    )


if __name__ == "__main__":
    main()
