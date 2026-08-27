"""Score resolved races and print a calibration summary (spec sections 34, 35).

    PYTHONPATH=. python scripts/run_scoring.py --resolutions data/seed/quant_example_resolutions.json
    PYTHONPATH=. python scripts/run_scoring.py --all
    PYTHONPATH=. python scripts/run_scoring.py --race nc-sen-2026 --calibration
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.database import get_session
from app.eval.calibration import build_calibration_report
from app.eval.resolutions import load_resolutions_file
from app.eval.scorer import score_all_resolved, score_resolved_race


def main() -> None:
    ap = argparse.ArgumentParser(description="Score resolved PPI races against outcomes.")
    ap.add_argument("--resolutions", type=Path, default=None, help="JSON file of race outcomes to load first")
    ap.add_argument("--race", default=None, help="score just this race_id")
    ap.add_argument("--all", action="store_true", help="score every race with a recorded resolution")
    ap.add_argument("--calibration", action="store_true", help="also print a calibration report")
    ap.add_argument("--group-by", default="series,horizon_days", help="calibration grouping dims")
    ap.add_argument("--dry-run", action="store_true", help="compute and print; roll back writes")
    args = ap.parse_args()

    out: dict = {}
    with get_session() as session:
        if args.resolutions:
            out["resolutions_loaded"] = load_resolutions_file(session, args.resolutions)

        if args.race:
            pts = score_resolved_race(session, args.race)
            out["scored"] = {args.race: len(pts)}
        elif args.all or args.resolutions:
            out["scored"] = score_all_resolved(session)
        else:
            out["scored"] = {}

        if args.calibration:
            report = build_calibration_report(
                session, group_by=tuple(d.strip() for d in args.group_by.split(",") if d.strip())
            )
            out["calibration"] = report.as_dict()

        if args.dry_run:
            session.rollback()
            out["dry_run"] = True

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
