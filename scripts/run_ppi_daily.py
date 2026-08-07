from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.ppi.pipeline import run_daily_pipeline

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the idempotent PPI daily pipeline")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--strict-llm-only",
        action="store_true",
        help=(
            "Canonical blind-Qwen-only mode: every evidence classification and every market "
            "forecast must come from the live LLM_PROVIDER (ollama/openai_compatible); a failure "
            "is recorded explicitly rather than silently substituted with a deterministic "
            "fallback. Refuses to run at all if LLM_PROVIDER is not set to a live provider."
        ),
    )
    parser.add_argument(
        "--lock-path",
        default=None,
        help="Override the concurrency lock file path (default: data/.ppi_pipeline.lock).",
    )
    args = parser.parse_args()
    result = run_daily_pipeline(
        args.trigger,
        args.force,
        strict_llm_only=args.strict_llm_only,
        lock_path=Path(args.lock_path) if args.lock_path else None,
    )
    print(json.dumps(result, indent=2, default=str))
    # LOCKED is benign: another run is genuinely in progress (the filesystem lock backstopping
    # the GitHub Actions concurrency group). Exit 0 so it never trips a failure notification.
    raise SystemExit(0 if result.get("status") in {"OK", "PARTIAL", "ALREADY_COMPLETE", "LOCKED"} else 1)
