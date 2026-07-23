from __future__ import annotations

import argparse
import json

from app.ppi.pipeline import run_daily_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the idempotent PPI daily pipeline")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = run_daily_pipeline(args.trigger, args.force)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("status") in {"OK", "PARTIAL", "ALREADY_COMPLETE"} else 1)
