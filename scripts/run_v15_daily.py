"""The PPI v1.5 twice-daily pipeline entry point (spec section 41).

    PYTHONPATH=. python scripts/run_v15_daily.py --offline --blind-stub
    PYTHONPATH=. python scripts/run_v15_daily.py --blind            # live providers (SKIPPED without keys)
    PYTHONPATH=. python scripts/run_v15_daily.py --run-key ppi-v15:2026-08-27:primary --discover

Stages 1-2 (market discovery + snapshot) need Polymarket network access and are opt-in
(``--discover`` / a wired market client). Stages 3-10 run on the ingested DB tables. The public
headline series is NOT changed here -- see docs/research/PPI_CUTOVER.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.database import get_session
from app.pipeline_v15.orchestrator import default_run_key, run_v15_pipeline

REAL_RACES = Path("data/seed/races_2026.json")
EXAMPLE_RACES = Path("data/seed/quant_example_races.json")


def _default_races() -> Path:
    """Prefer the real 2026 race set; fall back to the synthetic example set."""
    return REAL_RACES if REAL_RACES.exists() else EXAMPLE_RACES


def _race_configs(path: Path) -> tuple[list[dict], int]:
    doc = json.loads(path.read_text())
    cycle = int(doc.get("cycle", 2026))
    configs = []
    for r in doc.get("races", []):
        cycle = int(r.get("cycle", cycle))
        configs.append({
            "race_id": r["race_id"], "state": r["state"], "office": r["office"], "cycle": int(r["cycle"]),
            "election_date": r.get("election_date") or doc.get("election_date"),
            "dem_candidate": r.get("dem_candidate"), "rep_candidate": r.get("rep_candidate"),
            "incumbent_party": r.get("incumbent_party"),
            "source": f"seed:{path.stem}",
        })
    return configs, cycle


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the PPI v1.5 twice-daily pipeline. Default: Quant series only, no LLM cost "
        "(add --blind for the GPT/Claude benchmarks + a real ensemble)."
    )
    ap.add_argument("--races", type=Path, default=None,
                    help="race-set JSON (default: data/seed/races_2026.json, else the example set)")
    ap.add_argument("--run-key", default=None)
    ap.add_argument("--offline", action="store_true", help="use seed-file provider chains (no network/keys)")
    ap.add_argument("--blind", action="store_true", help="also run live GPT/Claude blind benchmarks + real ensemble")
    ap.add_argument("--blind-stub", action="store_true", help="run the deterministic blind stub (STUB rows)")
    ap.add_argument("--discover", action="store_true",
                    help="run stage 1 Polymarket market discovery (auto-binds supported races; needs network)")
    args = ap.parse_args()
    args.races = args.races or _default_races()

    configs, cycle = _race_configs(args.races)
    blind_mode = "stub" if args.blind_stub else ("live" if args.blind else None)

    ingest_kwargs = {}
    if args.offline:
        from app.providers.offline import (
            offline_candidate_chain_factory,
            offline_election_history_chain,
            offline_generic_ballot_chain,
            offline_poll_chain,
        )

        ingest_kwargs = dict(
            poll_chain=offline_poll_chain(args.races),
            generic_ballot_chain=offline_generic_ballot_chain(args.races),
            election_history_chain=offline_election_history_chain(),
            candidate_chain_factory=offline_candidate_chain_factory(args.races),
        )

    discovery_provider = None
    if args.discover:
        from app.providers.markets import PolymarketDiscoveryProvider

        discovery_provider = PolymarketDiscoveryProvider()

    with get_session() as session:
        summary = run_v15_pipeline(
            session,
            race_configs=configs,
            cycle=cycle,
            run_key=args.run_key or default_run_key(),
            blind_mode=blind_mode,
            discovery_provider=discovery_provider,
            ingest_kwargs=ingest_kwargs,
        )
    print(json.dumps(summary.as_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
