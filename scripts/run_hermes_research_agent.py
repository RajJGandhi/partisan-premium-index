#!/usr/bin/env python3
"""
scripts/run_hermes_research_agent.py

Hermes Research Agent v0.3

Daily public-source research courier for Reality Spread.

v0.3 fixes:
  - Adds false-positive guardrails for role/entity collision articles.
  - Stops foreign-leader visit articles from ranking strong for "Next PM" markets.
  - Makes PM/leadership markets require election, party, polling, coalition, or government-change context.

Earlier v0.2 fixes:
  - Stable briefing filenames based on target_id.
  - index.md for each daily briefing folder.
  - Source errors written to hermes_research_errors_latest.csv, not mixed into briefs.
  - Relevance scoring with strong / broad / weak buckets.
  - Option to disable GDELT if rate-limited.
  - Better query construction for parent-level markets.
  - No market prices, bids, asks, spreads, comparison_price, or signal gaps.

No API keys required.

Recommended Week 1 run:
  PYTHONPATH=. python scripts/run_hermes_research_agent.py \
    --scope parents \
    --days 7 \
    --max-results-per-query 6 \
    --disable-gdelt \
    --print-summary
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


RESULT_COLUMNS = [
    "run_date",
    "timestamp_utc",
    "target_id",
    "target_type",
    "target_name",
    "parent_market_name",
    "primary_outcome_to_track",
    "query",
    "source_kind",
    "source_name",
    "published_at",
    "title",
    "url",
    "relevance_score",
    "relevance_bucket",
    "matched_terms",
    "market_safety",
]

ERROR_COLUMNS = [
    "run_date",
    "timestamp_utc",
    "target_id",
    "target_type",
    "target_name",
    "query",
    "source_kind",
    "error_type",
    "error_message",
    "url",
]

ALERT_COLUMNS = ["severity", "area", "message", "recommended_action"]

GENERIC_TERMS = {
    "will",
    "win",
    "winner",
    "election",
    "race",
    "control",
    "after",
    "next",
    "general",
    "legislative",
    "party",
    "parti",
    "most",
    "seats",
    "seat",
    "senate",
    "house",
    "governor",
    "prime",
    "minister",
    "presidential",
    "president",
    "midterm",
    "midterms",
    "2026",
    "2027",
    "2028",
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "market",
    "parent",
    "briefing",
    "outcome",
    "candidate",
    "candidates",
    "democratic",
    "democrat",
    "republican",
    "republicans",
    "democrats",
}

IMPORTANT_GEOGRAPHY = {
    "alaska",
    "maine",
    "texas",
    "nebraska",
    "michigan",
    "ohio",
    "north",
    "carolina",
    "georgia",
    "hampshire",
    "iowa",
    "nevada",
    "arizona",
    "washington",
    "pennsylvania",
    "brazil",
    "sweden",
    "quebec",
    "vancouver",
    "israel",
    "zealand",
    "toronto",
}

MARKET_SOURCE_EXCLUSIONS = "-Polymarket -Kalshi -Metaculus -Manifold -PredictIt"

FOREIGN_VISIT_PATTERNS = [
    "modi",
    "indian pm",
    "india's modi",
    "india prime minister",
    "indian prime minister",
    "visit to new zealand",
    "visit new zealand",
    "to visit new zealand",
    "host indian prime minister",
    "official visit to new zealand",
]

ELECTION_RELEVANCE_TERMS = {
    "election",
    "poll",
    "polls",
    "polling",
    "campaign",
    "candidate",
    "candidates",
    "leader",
    "leadership",
    "opposition",
    "government",
    "coalition",
    "party",
    "parliament",
    "parliamentary",
    "seat",
    "seats",
    "race",
    "vote",
    "voters",
    "primary",
    "ballot",
    "nomination",
    "endorsement",
    "fundraising",
    "turnout",
    "preferred prime minister",
    "approval",
    "disapproval",
    "resign",
    "resignation",
}

PM_ROLE_TERMS = {
    "prime minister",
    "premier",
    "leader",
    "leadership",
    "government",
    "coalition",
    "preferred prime minister",
    "opposition leader",
    "party leader",
}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: Optional[dt.datetime] = None) -> str:
    return (ts or utc_now()).isoformat().replace("+00:00", "Z")


def slugify(text: str, max_len: int = 72) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len].strip("-") or "briefing"


def short_hash(text: str, n: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def read_csv(path: str | Path) -> List[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: str | Path, rows: List[Dict[str, Any]], columns: Sequence[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: str | Path, obj: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_pub_date(value: str) -> str:
    if not value:
        return ""
    try:
        d = email.utils.parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return value


def days_ago_cutoff(days: int) -> dt.datetime:
    return utc_now() - dt.timedelta(days=days)


def is_recent(pub: str, days: int) -> bool:
    if not pub:
        return True
    try:
        text = pub.replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(text)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc) >= days_ago_cutoff(days)
    except Exception:
        return True


def tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text.lower()) if len(t) >= 3]


def target_terms(target: Dict[str, str]) -> List[str]:
    parts = [
        target.get("parent_market_name", ""),
        target.get("primary_outcome_to_track", ""),
        target.get("query", ""),
        target.get("target_name", ""),
    ]
    toks = tokenize(" ".join(parts))
    cleaned = []
    for t in toks:
        if t in GENERIC_TERMS:
            continue
        if t not in cleaned:
            cleaned.append(t)
    return cleaned[:14]


def phrase_in(text: str, phrases: set[str] | list[str]) -> bool:
    hay = text.lower()
    return any(p.lower() in hay for p in phrases)


def has_any_token_or_phrase(text: str, terms: set[str] | list[str]) -> bool:
    hay = text.lower()
    toks = set(tokenize(hay))
    for term in terms:
        t = term.lower()
        if " " in t:
            if t in hay:
                return True
        elif t in toks:
            return True
    return False


def country_anchor_for_parent(parent: str) -> str:
    p = parent.lower()
    if "new zealand" in p:
        return "new zealand"
    if "sweden" in p:
        return "sweden"
    if "israel" in p or "israeli" in p:
        return "israel"
    if "brazil" in p:
        return "brazil"
    if "quebec" in p:
        return "quebec"
    if "vancouver" in p:
        return "vancouver"
    return ""


def is_next_pm_like_market(parent: str) -> bool:
    p = parent.lower()
    return "next prime minister" in p or "prime minister of" in p or p.startswith("next pm")


def event_relevance_guardrail(target: Dict[str, str], title: str, source: str = "") -> tuple[int, str]:
    """
    Returns (penalty_or_bonus, note).

    Purpose:
      Stop Hermes from marking entity-collision articles as strong evidence.
      Example false positive:
        "Indian PM Modi to visit New Zealand" for "Next Prime Minister of New Zealand".

    The brief should favor election/leadership/government evidence, not any article
    that happens to contain "Prime Minister" and the country name.
    """
    parent = target.get("parent_market_name", "")
    hay = f"{title} {source}".lower()

    if is_next_pm_like_market(parent):
        country = country_anchor_for_parent(parent)

        if country and country in hay and phrase_in(hay, FOREIGN_VISIT_PATTERNS):
            return -8, "foreign_leader_visit_collision"

        has_pm_role = has_any_token_or_phrase(hay, PM_ROLE_TERMS)
        has_election_context = has_any_token_or_phrase(hay, ELECTION_RELEVANCE_TERMS)

        if country and country in hay and has_pm_role and not has_election_context:
            return -5, "country_pm_without_contest_context"

        if country and country in hay and has_election_context:
            return 3, "country_with_contest_context"

    p = parent.lower()
    if "control" in p or "seats after" in p:
        if not has_any_token_or_phrase(
            hay, {"election", "poll", "polls", "midterm", "midterms", "campaign", "senate", "house", "seat", "seats"}
        ):
            return -3, "chamber_market_without_election_context"

    return 0, ""


def relevance_score(target: Dict[str, str], title: str, source: str = "") -> Tuple[int, List[str], str]:
    terms = target_terms(target)
    hay = " ".join([title, source]).lower()

    matched = []
    score = 0

    for t in terms:
        if t in hay:
            matched.append(t)
            score += 2 if t in IMPORTANT_GEOGRAPHY else 1

    parent = target.get("parent_market_name", "").lower()
    outcome = target.get("primary_outcome_to_track", "").lower()

    geography_hits = [t for t in matched if t in IMPORTANT_GEOGRAPHY]
    if geography_hits:
        score += 3

    outcome_terms = [t for t in tokenize(outcome) if t not in GENERIC_TERMS]
    outcome_hits = [t for t in outcome_terms if t in hay]
    if outcome_hits:
        score += 4

    if any(
        x in parent
        for x in ["alaska", "maine", "texas", "nebraska", "michigan", "ohio", "georgia", "new hampshire", "iowa"]
    ):
        if "senate" in parent and "senate" in hay:
            score += 1
        if not geography_hits and "senate" in hay:
            score -= 2

    if any(x in parent for x in ["governor", "mayoral", "premier", "prime minister"]):
        if not geography_hits and not outcome_hits:
            score -= 3

    if "poll" in hay or "polls" in hay:
        score += 1

    guardrail_delta, guardrail_note = event_relevance_guardrail(target, title, source)
    score += guardrail_delta
    if guardrail_note:
        matched.append(f"guardrail:{guardrail_note}")

    if is_next_pm_like_market(parent):
        if score >= 7:
            bucket = "strong"
        elif score >= 4:
            bucket = "broad"
        else:
            bucket = "weak"
    else:
        if score >= 6:
            bucket = "strong"
        elif score >= 3:
            bucket = "broad"
        else:
            bucket = "weak"

    return score, matched, bucket


def google_news_rss_url(query: str, days: int) -> str:
    q = f"{query} when:{days}d"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {
            "q": q,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
    )


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RealitySpreadHermes/0.3 (+local research script)",
            "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def make_error(
    run_date: str,
    timestamp: str,
    target: Dict[str, str],
    query: str,
    source_kind: str,
    error_type: str,
    error_message: str,
    url: str,
) -> Dict[str, Any]:
    return {
        "run_date": run_date,
        "timestamp_utc": timestamp,
        "target_id": target.get("target_id", ""),
        "target_type": target.get("target_type", ""),
        "target_name": target.get("target_name", ""),
        "query": query,
        "source_kind": source_kind,
        "error_type": error_type,
        "error_message": error_message[:500],
        "url": url,
    }


def fetch_google_news(
    query: str,
    days: int,
    max_results: int,
    target: Dict[str, str],
    errors: List[Dict[str, Any]],
    run_date: str,
    timestamp: str,
) -> List[Dict[str, Any]]:
    url = google_news_rss_url(query, days)
    try:
        raw = fetch_url(url)
        root = ET.fromstring(raw)
    except Exception as exc:
        errors.append(
            make_error(run_date, timestamp, target, query, "google_news_rss", type(exc).__name__, str(exc), url)
        )
        return []

    rows = []
    items = root.findall(".//item")
    for item in items[: max_results * 3]:
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        pub = parse_pub_date((item.findtext("pubDate") or "").strip())
        source_el = item.find("source")
        source_name = (
            html.unescape(source_el.text.strip()) if source_el is not None and source_el.text else "Google News"
        )

        if not title:
            continue
        if not is_recent(pub, days):
            continue

        score, matched, bucket = relevance_score(target, title, source_name)
        rows.append(
            {
                "source_kind": "google_news_rss",
                "source_name": source_name,
                "published_at": pub,
                "title": title,
                "url": link,
                "relevance_score": score,
                "relevance_bucket": bucket,
                "matched_terms": ",".join(matched),
            }
        )
    return rows[:max_results]


def gdelt_url(query: str, max_results: int) -> str:
    return "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_results),
            "sort": "DateDesc",
        }
    )


def fetch_gdelt(
    query: str,
    days: int,
    max_results: int,
    target: Dict[str, str],
    errors: List[Dict[str, Any]],
    run_date: str,
    timestamp: str,
) -> List[Dict[str, Any]]:
    url = gdelt_url(query, max_results=max_results)
    try:
        raw = fetch_url(url)
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        errors.append(make_error(run_date, timestamp, target, query, "gdelt_doc", type(exc).__name__, str(exc), url))
        return []

    rows = []
    for article in data.get("articles", [])[: max_results * 2]:
        title = html.unescape(str(article.get("title", "")).strip())
        link = str(article.get("url", "")).strip()
        source_name = str(article.get("domain", "") or article.get("sourceCountry", "") or "GDELT").strip()
        seendate = str(article.get("seendate", "")).strip()
        pub = ""
        if seendate:
            try:
                pub_dt = dt.datetime.strptime(seendate[:15], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
                pub = iso(pub_dt)
            except Exception:
                pub = seendate

        if not title:
            continue
        if not is_recent(pub, days):
            continue

        score, matched, bucket = relevance_score(target, title, source_name)
        rows.append(
            {
                "source_kind": "gdelt_doc",
                "source_name": source_name,
                "published_at": pub,
                "title": title,
                "url": link,
                "relevance_score": score,
                "relevance_bucket": bucket,
                "matched_terms": ",".join(matched),
            }
        )
    return rows[:max_results]


def build_parent_targets(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_parent: Dict[str, Dict[str, str]] = {}
    for r in rows:
        parent = r.get("parent_market_name", "").strip() or "Unknown Parent"
        if parent in by_parent:
            continue
        by_parent[parent] = {
            "target_id": f"PARENT-{slugify(parent, 48)}-{short_hash(parent, 6)}",
            "target_type": "parent",
            "target_name": parent,
            "parent_market_name": parent,
            "primary_outcome_to_track": "Parent market briefing",
            "query": parent,
        }
    return list(by_parent.values())


def build_all_targets(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    targets = []
    for r in rows:
        tid = r.get("tracking_id", "").strip()
        parent = r.get("parent_market_name", "").strip()
        outcome = r.get("primary_outcome_to_track", "").strip()
        if not tid:
            tid = f"ROW-{short_hash(parent + outcome)}"
        name = f"{parent} — {outcome}".strip(" —")
        query = r.get("search_query", "").strip() or name
        targets.append(
            {
                "target_id": tid,
                "target_type": "row",
                "target_name": name,
                "parent_market_name": parent,
                "primary_outcome_to_track": outcome,
                "query": query,
            }
        )
    return targets


def expand_query(target: Dict[str, str]) -> str:
    parent = target.get("parent_market_name", "")
    outcome = target.get("primary_outcome_to_track", "")
    query = target.get("query", "")
    if target.get("target_type") == "parent":
        q = parent
    else:
        if outcome and outcome.lower() not in {"parent market briefing", "other"}:
            q = f"{parent} {outcome}"
        else:
            q = query or parent
    q = re.sub(r"\s+", " ", q).strip()
    return f"{q} {MARKET_SOURCE_EXCLUSIONS}"


def dedupe_results(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        key = r.get("url") or r.get("title")
        if not key:
            continue
        norm = str(key).split("?")[0].strip().lower()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(r)
    return out


def enrich_result(
    run_date: str, timestamp: str, target: Dict[str, str], query: str, r: Dict[str, Any]
) -> Dict[str, Any]:
    out = {
        "run_date": run_date,
        "timestamp_utc": timestamp,
        "target_id": target.get("target_id", ""),
        "target_type": target.get("target_type", ""),
        "target_name": target.get("target_name", ""),
        "parent_market_name": target.get("parent_market_name", ""),
        "primary_outcome_to_track": target.get("primary_outcome_to_track", ""),
        "query": query,
        "market_safety": "NO_MARKET_PRICES_INCLUDED",
    }
    out.update(r)
    return out


def write_brief(target: Dict[str, str], rows: List[Dict[str, Any]], out_dir: Path, timestamp: str, days: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{target['target_id']}.md"

    strong = [r for r in rows if r.get("relevance_bucket") == "strong"]
    broad = [r for r in rows if r.get("relevance_bucket") == "broad"]
    weak = [r for r in rows if r.get("relevance_bucket") == "weak"]

    def table(section_rows: List[Dict[str, Any]], limit: int = 12) -> str:
        if not section_rows:
            return "_None._"
        lines = [
            "| Source | Publisher/source | Published/seen | Relevance | Title | URL |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in section_rows[:limit]:
            title = str(r.get("title", "")).replace("|", "\\|")
            if len(title) > 115:
                title = title[:112] + "..."
            url = str(r.get("url", ""))
            if len(url) > 120:
                url = url[:117] + "..."
            rel = f"{r.get('relevance_bucket', '')} ({r.get('relevance_score', '')})"
            lines.append(
                f"| {r.get('source_kind', '')} | {r.get('source_name', '')} | {r.get('published_at', '')} | {rel} | {title} | {url} |"
            )
        return "\n".join(lines)

    text = f"""# Hermes Research Brief — {target["target_id"]}

Generated: {timestamp}
Lookback window: last {days} days

## Market

- Parent: {target.get("parent_market_name", "")}
- Outcome: {target.get("primary_outcome_to_track", "")}
- Target type: {target.get("target_type", "")}
- Target name: {target.get("target_name", "")}

## Experiment safety note

This brief is a public-source research digest. It intentionally does not include market prices, bids, asks, spreads, comparison_price, signal gaps, or prediction-market odds.

## Relevance summary

- Strong hits: {len(strong)}
- Broad hits: {len(broad)}
- Weak hits retained for audit only: {len(weak)}

## Strong source hits

{table(strong)}

## Broad/context source hits

{table(broad)}

## Weak/noisy hits retained for audit

{table(weak[:5], limit=5)}

## Notes for analyst / LLM evidence layer

- Treat these as leads, not verified facts.
- Strong hits are the best candidates for evidence updates.
- Broad hits may be useful for national-cycle context but should not override market-specific evidence.
- Weak hits are retained for audit/debugging and should generally not be used in forecast evidence.
- Prefer primary sources, election authorities, reputable polling aggregators, official party pages, and established news outlets.
- If no strong recent hits appear, preserve the previous evidence and mark this market as low-news rather than inventing updates.
"""
    path.write_text(text, encoding="utf-8")
    return path


def write_index(
    targets: List[Dict[str, str]], result_rows: List[Dict[str, Any]], brief_dir: Path, timestamp: str, run_date: str
) -> None:
    counts: Dict[str, Dict[str, int]] = {}
    for r in result_rows:
        tid = r.get("target_id", "")
        counts.setdefault(tid, {"strong": 0, "broad": 0, "weak": 0, "total": 0})
        bucket = str(r.get("relevance_bucket", "weak"))
        if bucket not in counts[tid]:
            counts[tid][bucket] = 0
        counts[tid][bucket] += 1
        counts[tid]["total"] += 1

    lines = [
        f"# Hermes Research Briefing Index — {run_date}",
        "",
        f"Generated: {timestamp}",
        "",
        "| Target | Parent | Strong | Broad | Weak | Total | Brief |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]

    for t in targets:
        tid = t["target_id"]
        c = counts.get(tid, {"strong": 0, "broad": 0, "weak": 0, "total": 0})
        parent_safe = t.get("parent_market_name", "").replace("|", "\\|")
        filename = f"{tid}.md"
        lines.append(
            f"| {tid} | {parent_safe} | {c.get('strong', 0)} | {c.get('broad', 0)} | {c.get('weak', 0)} | {c.get('total', 0)} | [{filename}]({filename}) |"
        )

    (brief_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def maybe_write_evidence_overlay(targets: List[Dict[str, str]], brief_paths: Dict[str, Path], run_date: str) -> None:
    latest_dir = Path("evidence/research_briefs/latest")
    dated_dir = Path("evidence/research_briefs") / run_date
    latest_dir.mkdir(parents=True, exist_ok=True)
    dated_dir.mkdir(parents=True, exist_ok=True)

    for t in targets:
        tid = t["target_id"]
        src = brief_paths.get(tid)
        if not src or not src.exists():
            continue
        text = src.read_text(encoding="utf-8")
        (latest_dir / f"{tid}.md").write_text(text, encoding="utf-8")
        (dated_dir / f"{tid}.md").write_text(text, encoding="utf-8")


def build_alerts(
    result_rows: List[Dict[str, Any]], errors: List[Dict[str, Any]], targets: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    alerts = []
    if errors:
        alerts.append(
            {
                "severity": "WARN",
                "area": "source_fetch",
                "message": f"{len(errors)} source fetch errors were recorded.",
                "recommended_action": "Inspect data/research/hermes_research_errors_latest.csv.",
            }
        )

    strong_by_target = {}
    for r in result_rows:
        if r.get("relevance_bucket") == "strong":
            strong_by_target[r.get("target_id", "")] = strong_by_target.get(r.get("target_id", ""), 0) + 1

    no_strong = [t for t in targets if strong_by_target.get(t["target_id"], 0) == 0]
    if no_strong:
        alerts.append(
            {
                "severity": "INFO",
                "area": "relevance",
                "message": f"{len(no_strong)} targets had no strong recent source hits.",
                "recommended_action": "Use broad context only; preserve existing evidence for low-news markets.",
            }
        )
    return alerts


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run Hermes public-source research agent.")
    p.add_argument("--markets", default="data/tracked_markets_final.csv")
    p.add_argument("--scope", choices=["parents", "all"], default="parents")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--max-results-per-query", type=int, default=6)
    p.add_argument("--sleep-seconds", type=float, default=0.25)
    p.add_argument("--gdelt-sleep-seconds", type=float, default=1.0)
    p.add_argument("--disable-gdelt", action="store_true")
    p.add_argument("--write-evidence-overlay", action="store_true")
    p.add_argument("--print-summary", action="store_true")
    p.add_argument("--output-root", default="data/research")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    now = utc_now()
    timestamp = iso(now)
    run_date = now.date().isoformat()

    market_rows = read_csv(args.markets)
    if not market_rows:
        raise SystemExit(f"No market rows found: {args.markets}")

    targets = build_parent_targets(market_rows) if args.scope == "parents" else build_all_targets(market_rows)

    output_root = Path(args.output_root)
    daily_root = output_root / "daily" / run_date
    brief_dir = daily_root / ("parent_briefings" if args.scope == "parents" else "briefings")
    brief_dir.mkdir(parents=True, exist_ok=True)

    result_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    brief_paths: Dict[str, Path] = {}

    for i, target in enumerate(targets, 1):
        query = expand_query(target)
        print(f"[{i}/{len(targets)}] {target['target_id']} — {target['target_name']}")

        raw_results: List[Dict[str, Any]] = []
        raw_results.extend(
            fetch_google_news(query, args.days, args.max_results_per_query, target, errors, run_date, timestamp)
        )
        time.sleep(max(args.sleep_seconds, 0))

        if not args.disable_gdelt:
            raw_results.extend(
                fetch_gdelt(query, args.days, args.max_results_per_query, target, errors, run_date, timestamp)
            )
            time.sleep(max(args.gdelt_sleep_seconds, args.sleep_seconds, 0))

        raw_results = dedupe_results(raw_results)
        enriched = [enrich_result(run_date, timestamp, target, query, r) for r in raw_results]
        result_rows.extend(enriched)

        enriched_sorted = sorted(
            enriched,
            key=lambda r: (int(r.get("relevance_score", 0)), str(r.get("published_at", ""))),
            reverse=True,
        )
        brief_paths[target["target_id"]] = write_brief(target, enriched_sorted, brief_dir, timestamp, args.days)

    write_index(targets, result_rows, brief_dir, timestamp, run_date)

    if args.write_evidence_overlay:
        maybe_write_evidence_overlay(targets, brief_paths, run_date)

    alerts = build_alerts(result_rows, errors, targets)

    write_csv(output_root / "hermes_research_latest.csv", result_rows, RESULT_COLUMNS)
    write_csv(output_root / f"hermes_research_{run_date}.csv", result_rows, RESULT_COLUMNS)
    write_csv(output_root / "hermes_research_errors_latest.csv", errors, ERROR_COLUMNS)
    write_csv(output_root / f"hermes_research_errors_{run_date}.csv", errors, ERROR_COLUMNS)
    write_csv(output_root / "hermes_research_alerts_latest.csv", alerts, ALERT_COLUMNS)

    bucket_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for r in result_rows:
        bucket_counts[r.get("relevance_bucket", "")] = bucket_counts.get(r.get("relevance_bucket", ""), 0) + 1
        source_counts[r.get("source_name", "")] = source_counts.get(r.get("source_name", ""), 0) + 1

    manifest = {
        "timestamp_utc": timestamp,
        "run_date": run_date,
        "status": "WARNINGS" if alerts else "OK",
        "markets_input": args.markets,
        "scope": args.scope,
        "target_rows": len(targets),
        "results_total": len(result_rows),
        "errors_total": len(errors),
        "brief_dir": str(brief_dir),
        "index_path": str(brief_dir / "index.md"),
        "latest_csv": str(output_root / "hermes_research_latest.csv"),
        "latest_errors_csv": str(output_root / "hermes_research_errors_latest.csv"),
        "alerts_csv": str(output_root / "hermes_research_alerts_latest.csv"),
        "relevance_bucket_counts": bucket_counts,
        "source_counts_top": dict(sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True)[:20]),
        "alerts": alerts,
        "settings": {
            "days": args.days,
            "max_results_per_query": args.max_results_per_query,
            "disable_gdelt": args.disable_gdelt,
            "sleep_seconds": args.sleep_seconds,
            "gdelt_sleep_seconds": args.gdelt_sleep_seconds,
            "write_evidence_overlay": args.write_evidence_overlay,
        },
    }

    write_json(output_root / "hermes_research_manifest_latest.json", manifest)
    write_json(output_root / f"hermes_research_manifest_{run_date}.json", manifest)
    write_json("data/health/latest_hermes_research_health.json", manifest)

    print("\nHermes research complete.")
    print(f"Status: {manifest['status']}")
    print(f"Targets: {len(targets)}")
    print(f"Results: {len(result_rows)}")
    print(f"Errors: {len(errors)}")
    print(f"Relevance buckets: {bucket_counts}")
    print(f"Latest CSV: {manifest['latest_csv']}")
    print(f"Errors CSV: {manifest['latest_errors_csv']}")
    print(f"Briefings index: {manifest['index_path']}")

    if args.print_summary:
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "target_rows": manifest["target_rows"],
                    "results_total": manifest["results_total"],
                    "errors_total": manifest["errors_total"],
                    "relevance_bucket_counts": manifest["relevance_bucket_counts"],
                    "alerts": manifest["alerts"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
