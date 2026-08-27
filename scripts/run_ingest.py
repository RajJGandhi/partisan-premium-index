"""Run the automated political-data ingestion (spec section 41, stages 3-4).

Fetches historical presidential results, the generic ballot, candidate/incumbency metadata and
race polls through the provider chains and writes the normalized, de-duplicated observations to
the append-only DB tables the Quant engine reads. Never writes prediction-market data.

    PYTHONPATH=. python scripts/run_ingest.py --offline            # no network/keys: seed-file chains
    PYTHONPATH=. python scripts/run_ingest.py                       # live chains (graceful-degrade w/o keys)
    PYTHONPATH=. python scripts/run_ingest.py --offline --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.database import get_session
from app.providers.ingest import ingest_political_data
from app.providers.offline import (
    offline_candidate_chain_factory,
    offline_election_history_chain,
    offline_generic_ballot_chain,
    offline_poll_chain,
)

DEFAULT_RACES = Path("data/seed/quant_example_races.json")


def _race_configs(races_path: Path) -> tuple[list[dict], int]:
    doc = json.loads(races_path.read_text())
    configs = []
    cycle = 2026
    for r in doc.get("races", []):
        cycle = int(r.get("cycle", cycle))
        configs.append(
            {
                "race_id": r["race_id"],
                "state": r["state"],
                "office": r["office"],
                "cycle": int(r["cycle"]),
                "election_date": r.get("election_date"),
                "dem_candidate": r.get("dem_candidate"),
                "rep_candidate": r.get("rep_candidate"),
                "incumbent_party": r.get("incumbent_party"),
            }
        )
    return configs, cycle


def main() -> None:
    ap = argparse.ArgumentParser(description="Run PPI political-data ingestion.")
    ap.add_argument("--races", type=Path, default=DEFAULT_RACES)
    ap.add_argument("--offline", action="store_true", help="use seed-file chains (no network/keys)")
    ap.add_argument("--dry-run", action="store_true", help="run chains, print summary, roll back writes")
    ap.add_argument("--no-cache", action="store_true", help="bypass the provider response cache")
    args = ap.parse_args()

    configs, cycle = _race_configs(args.races)

    kwargs: dict = {"cycle": cycle, "allow_cache": not args.no_cache}
    if args.offline:
        kwargs.update(
            poll_chain=offline_poll_chain(args.races),
            generic_ballot_chain=offline_generic_ballot_chain(args.races),
            election_history_chain=offline_election_history_chain(),
            candidate_chain_factory=offline_candidate_chain_factory(args.races),
        )

    with get_session() as session:
        summary = ingest_political_data(session, configs, **kwargs)
        out = summary.as_dict()
        if args.dry_run:
            session.rollback()
            out["dry_run"] = True
        print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
