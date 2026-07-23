#!/usr/bin/env python3
"""
scripts/create_evidence_skeletons.py

Creates starter evidence packet files for the LLM fair-value runner.
It will not overwrite existing files unless --overwrite is passed.

Run:
    PYTHONPATH=. python scripts/create_evidence_skeletons.py \
      --markets data/tracked_markets_final.csv \
      --evidence-root evidence
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List


def slugify(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def write_if_missing(path: Path, text: str, overwrite: bool = False) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def shared_template(name: str) -> str:
    return f"""# {name}

## Static context

Add stable facts, electoral rules, institutions, likely candidates/parties, and baseline assumptions.

## Current evidence summary

Add recent polls, candidate developments, campaign news, macro context, and major uncertainties.

## Update log

- YYYY-MM-DD:
"""


def parent_template(parent_name: str, region: str, bucket: str, system_type: str, event_group: str) -> str:
    return f"""# {parent_name}

Region: {region}
Bucket: {bucket}
System type: {system_type}
Underlying event group: {event_group}

## Resolution / contract notes

Describe what the parent market is asking and any important resolution rules.

## Base-rate considerations

Add base rates or structural context.

## Current evidence

Add polls, candidate information, recent news, endorsements, legal/institutional context, etc.

## What would change the estimate?

List evidence that should move fair value.

## Update log

- YYYY-MM-DD:
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Create evidence packet skeletons.")
    parser.add_argument("--markets", default="data/tracked_markets_final.csv")
    parser.add_argument("--evidence-root", default="evidence")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = Path(args.evidence_root)
    rows = read_rows(Path(args.markets))

    created = 0

    shared_files = {
        "shared/us_midterms_context.md": shared_template("US Midterms Context"),
        "shared/brazil_context.md": shared_template("Brazil Election Context"),
        "shared/global_satellite_context.md": shared_template("Global Satellite Election Context"),
    }

    for rel, text in shared_files.items():
        if write_if_missing(root / rel, text, overwrite=args.overwrite):
            created += 1

    seen_parents = set()
    for row in rows:
        key = row.get("underlying_event_group") or row.get("parent_market_name")
        if not key or key in seen_parents:
            continue
        seen_parents.add(key)

        parent_name = row.get("parent_market_name", "")
        rel = root / "parents" / f"{slugify(key)}.md"
        text = parent_template(
            parent_name=parent_name,
            region=row.get("region", ""),
            bucket=row.get("bucket", ""),
            system_type=row.get("system_type", ""),
            event_group=row.get("underlying_event_group", ""),
        )
        if write_if_missing(rel, text, overwrite=args.overwrite):
            created += 1

    (root / "markets").mkdir(parents=True, exist_ok=True)

    print(f"Evidence root: {root}")
    print(f"Parent evidence files considered: {len(seen_parents)}")
    print(f"Files created/overwritten: {created}")


if __name__ == "__main__":
    main()
