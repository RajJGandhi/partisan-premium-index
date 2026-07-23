#!/usr/bin/env python3
"""
scripts/fix_v6_market_universe.py

Cleans the over-aggressive v6 auto-resolved Polymarket universe.

What it does:
1. Drops parent markets we decided are fake/not worth tracking:
   - Brazil Presidential Election First Round: 1st Place
   - Florida Governor Republican Primary Winner / Fishback bucket

2. Marks likely nonexistent Republican Senate seat-count brackets 48-52 as REPLACE.

3. Resets known bad auto-accepted rows so they can be rerun with topic-gated resolver v7:
   - Arizona Governor
   - Brazil first-round margin bad options
   - São Paulo Governor
   - Quebec Premier
   - Quebec General Election non-PQ options
   - Toronto Mayor
   - New Zealand PM

4. Adds topic/outcome gates used by resolver v7:
   - topic_filter_all: pipe-separated terms that MUST appear in candidate question/slug/event.
   - outcome_filter_any: pipe-separated outcome aliases, at least one of which MUST appear.

Run:
    python scripts/fix_v6_market_universe.py \
      --input data/tracked_markets_resolved_v6.csv \
      --output data/tracked_markets_for_v7.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List


DROP_PARENTS = {
    "Brazil Presidential Election First Round: 1st Place",
    "Florida Governor Republican Primary Winner",
}

# These were mapped to Senate-control markets; if exact live 48-52 bracket contracts do not exist,
# they should not stay in the experiment.
SENATE_RANGE_REPLACE_OUTCOMES = {"48", "49", "50", "51", "52"}

# Known bad v6 rows that should be reset and rerun under v7.
RESET_TRACKING_IDS = {
    # Arizona governor
    "RSO-0050",
    "RSO-0051",
    # Brazil margin wrong option mappings
    "RSO-0093",
    "RSO-0097",
    "RSO-0102",
    "RSO-0103",
    # Sao Paulo governor
    "RSO-0155",
    "RSO-0156",
    "RSO-0157",
    "RSO-0158",
    "RSO-0159",
    # Quebec premier
    "RSO-0174",
    "RSO-0175",
    "RSO-0176",
    "RSO-0177",
    "RSO-0178",
    "RSO-0179",
    "RSO-0180",
    # Quebec winner non-PQ rows
    "RSO-0182",
    "RSO-0183",
    "RSO-0184",
    "RSO-0185",
    "RSO-0186",
    # Toronto mayor
    "RSO-0187",
    "RSO-0188",
    "RSO-0189",
    "RSO-0190",
    "RSO-0191",
    "RSO-0192",
    "RSO-0193",
    "RSO-0194",
    # New Zealand PM
    "RSO-0231",
    "RSO-0232",
    "RSO-0233",
    "RSO-0234",
    "RSO-0235",
    "RSO-0236",
    "RSO-0237",
}

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
    "topic_filter_all",
    "outcome_filter_any",
    "cut_reason",
    "fix_action",
]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def norm_ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")


def slugify(value: str) -> str:
    text = norm_ascii(value).lower()
    text = text.replace("&", " and ").replace("+", " plus ").replace("<", " less than ").replace(">", " greater than ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def clean_name(value: str) -> str:
    return re.sub(r"\s+\([A-Z]\)$", "", str(value)).strip()


def pipe_terms(*terms: str) -> str:
    return "|".join(t.strip() for t in terms if t and t.strip())


def party_aliases(outcome: str) -> str:
    o = outcome.lower()
    if "democrat" in o or "(d)" in o:
        return "democrat|democrats|democratic"
    if "republican" in o or "(r)" in o or "gop" in o:
        return "republican|republicans|gop"
    if "independent" in o:
        return "independent"
    return ""


def outcome_aliases(parent: str, outcome: str) -> str:
    name = clean_name(outcome)
    lower = name.lower()

    if parent == "Arizona Governor Election Winner":
        return party_aliases(outcome)

    if parent == "Brazil Presidential Election First Round: Margin of Victory":
        if "lula" in lower and "15" in lower:
            return "lula|luiz inacio|15%|15 percent|15 or more"
        if "flavio" in lower and "10" in lower:
            return "flavio|bolsonaro|10%|10 percent|10 or more"
        if "ratinho" in lower:
            return "ratinho|junior|júnior"
        if lower == "other":
            return "other|another|any other"
        return name

    if parent == "São Paulo Governor Election Winner":
        aliases = {
            "Tarcísio de Freitas": "tarcisio|tarcísio|freitas",
            "Fernando Haddad": "fernando|haddad",
            "Kim Kataguiri": "kim|kataguiri",
            "Márcio França": "marcio|márcio|franca|frança",
            "Erika Hilton": "erika|hilton",
        }
        return aliases.get(outcome, name)

    if parent == "Next Premier of Quebec":
        aliases = {
            "Paul St-Pierre Plamondon": "paul|st-pierre|st pierre|plamondon",
            "Charles Milliard": "charles|milliard",
            "Christine Fréchette": "christine|frechette|fréchette",
            "Bernard Drainville": "bernard|drainville",
            "Ruba Ghazal": "ruba|ghazal",
            "Sol Zanetti": "sol|zanetti",
            "Éric Duhaime": "eric|éric|duhaime",
        }
        return aliases.get(outcome, name)

    if parent == "Quebec General Election Winner":
        aliases = {
            "PQ": "parti quebecois|parti québécois|pq",
            "PLQ": "liberal|liberals|parti liberal|parti libéral|plq",
            "CAQ": "coalition avenir|caq",
            "PCQ": "conservative|conservateur|pcq",
            "QS": "quebec solidaire|québec solidaire|qs",
            "PVQ": "green|vert|pvq",
        }
        return aliases.get(outcome, outcome)

    if parent == "Toronto Mayoral Election Winner":
        aliases = {
            "Olivia Chow": "olivia|chow",
            "Brad Bradford": "brad|bradford",
            "Michael Ford": "michael|ford",
            "Anthony Furey": "anthony|furey",
            "Kevin Clarke": "kevin|clarke",
            "Ana Bailão": "ana|bailao|bailão",
            "Marco Mendicino": "marco|mendicino",
            "John Tory": "john|tory",
        }
        return aliases.get(outcome, name)

    if parent == "Next Prime Minister of New Zealand":
        aliases = {
            "Chris Hipkins": "chris|hipkins",
            "Christopher Luxon": "christopher|luxon",
            "Nicola Willis": "nicola|willis",
            "Winston Peters": "winston|peters",
            "Carmel Sepuloni": "carmel|sepuloni",
            "Chlöe Swarbrick": "chloe|chloë|swarbrick",
            "David Seymour": "david|seymour",
        }
        return aliases.get(outcome, name)

    return party_aliases(outcome) or name


def topic_terms(parent: str) -> str:
    if parent == "Arizona Governor Election Winner":
        return "arizona|governor"
    if parent == "Brazil Presidential Election First Round: Margin of Victory":
        return "brazil|first round|presidential"
    if parent == "São Paulo Governor Election Winner":
        return "sao paulo|governor"
    if parent == "Next Premier of Quebec":
        return "quebec|premier"
    if parent == "Quebec General Election Winner":
        return "quebec|general election"
    if parent == "Toronto Mayoral Election Winner":
        return "toronto|mayor"
    if parent == "Next Prime Minister of New Zealand":
        return "new zealand|prime minister"
    return ""


def stronger_query(parent: str, outcome: str) -> str:
    name = clean_name(outcome)

    if parent == "Arizona Governor Election Winner":
        p = "Democrats" if "democrat" in outcome.lower() else "Republicans"
        return f"Will the {p} win the Arizona governor race in 2026?"

    if parent == "Brazil Presidential Election First Round: Margin of Victory":
        low = outcome.lower()
        if "lula" in low and "15" in low:
            return "Will Luiz Inácio Lula da Silva win the first round of the 2026 Brazilian presidential election by 15% or more?"
        if "flávio" in low or "flavio" in low:
            if "10" in low and "+" in low:
                return "Will Flávio Bolsonaro win the first round of the 2026 Brazilian presidential election by 10% or more?"
        if "ratinho" in low:
            return "Will Ratinho Júnior win the first round of the 2026 Brazilian presidential election?"
        if low == "other":
            return "Will any other candidate win the first round of the 2026 Brazilian presidential election?"
        return f"Brazil Presidential Election First Round: Margin of Victory — {outcome}"

    if parent == "São Paulo Governor Election Winner":
        return f"Will {name} win the São Paulo Governor election?"

    if parent == "Next Premier of Quebec":
        return f"Will {name} be the next Premier of Quebec?"

    if parent == "Quebec General Election Winner":
        return f"Will {outcome} win the 2026 Quebec general election?"

    if parent == "Toronto Mayoral Election Winner":
        return f"Will {name} win the Toronto mayoral election?"

    if parent == "Next Prime Minister of New Zealand":
        return f"Will {name} be the next Prime Minister of New Zealand?"

    return f"{parent} {outcome}"


def apply_row_fixes(row: Dict[str, str]) -> Dict[str, str]:
    parent = row.get("parent_market_name", "")
    outcome = row.get("primary_outcome_to_track", "")
    tracking_id = row.get("tracking_id", "")

    row.setdefault("topic_filter_all", "")
    row.setdefault("outcome_filter_any", "")
    row.setdefault("cut_reason", "")
    row.setdefault("fix_action", "")

    # Mark likely nonexistent senate brackets as replace, not rerun.
    if parent == "Republican Senate seats after 2026 midterms" and outcome in SENATE_RANGE_REPLACE_OUTCOMES:
        for col in RESOLUTION_FIELDS:
            row[col] = ""
        row["verification_status"] = "REPLACE"
        row["status"] = "replace_no_live_contract_found"
        row["fix_action"] = "MARKED_REPLACE"
        row["resolver_notes"] = (
            "Marked REPLACE: v6 mapped this bracket to Senate-control; exact live seat-count contract not found in prior runs."
        )
        return row

    # Reset bad rows for v7.
    if tracking_id in RESET_TRACKING_IDS:
        q = stronger_query(parent, outcome)
        for col in RESOLUTION_FIELDS:
            row[col] = ""
        row["question"] = q
        row["search_query"] = q
        row["exact_polymarket_slug"] = "TODO_VERIFY"
        row["slug_search_hint"] = slugify(q)
        # Preserve parent slug if already present; otherwise slugify parent.
        if not row.get("parent_slug_search_hint"):
            row["parent_slug_search_hint"] = slugify(parent)
        row["status"] = "needs_v7_topic_gated_resolution"
        row["verification_status"] = ""
        row["topic_filter_all"] = topic_terms(parent)
        row["outcome_filter_any"] = outcome_aliases(parent, outcome)
        row["fix_action"] = "RESET_FOR_V7"
        row["resolver_notes"] = "Reset after v6 wrong auto-match; rerun with v7 topic/outcome gates."
        return row

    # Add gates to existing rows where useful, even if already verified.
    if not row.get("topic_filter_all"):
        row["topic_filter_all"] = topic_terms(parent)
    if not row.get("outcome_filter_any"):
        row["outcome_filter_any"] = outcome_aliases(parent, outcome)

    row["fix_action"] = row.get("fix_action") or "KEPT"
    return row


def parse_args():
    parser = argparse.ArgumentParser(description="Clean v6 market universe and prepare rerun with resolver v7.")
    parser.add_argument("--input", default="data/tracked_markets_resolved_v6.csv")
    parser.add_argument("--output", default="data/tracked_markets_for_v7.csv")
    parser.add_argument("--dropped-output", default="data/tracked_markets_cut_rows.csv")
    return parser.parse_args()


def main():
    args = parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    dropped_out = Path(args.dropped_output)

    with inp.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in ADDED_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    kept: List[Dict[str, str]] = []
    dropped: List[Dict[str, str]] = []

    for row in rows:
        parent = row.get("parent_market_name", "")
        if parent in DROP_PARENTS:
            row["cut_reason"] = "CUT_BY_USER: fake/not worth tracking"
            row["fix_action"] = "DROPPED"
            dropped.append(row)
            continue

        kept.append(apply_row_fixes(row))

    out.parent.mkdir(parents=True, exist_ok=True)
    dropped_out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    with dropped_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dropped)

    print(f"Input rows: {len(rows)}")
    print(f"Kept rows: {len(kept)}")
    print(f"Dropped rows: {len(dropped)}")
    print(f"Reset for v7: {sum(1 for r in kept if r.get('fix_action') == 'RESET_FOR_V7')}")
    print(f"Marked replace: {sum(1 for r in kept if r.get('fix_action') == 'MARKED_REPLACE')}")
    print(f"Wrote: {out}")
    print(f"Wrote dropped rows: {dropped_out}")


if __name__ == "__main__":
    main()
