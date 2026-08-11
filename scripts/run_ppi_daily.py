from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.db.database import get_session
from app.ppi.pipeline import run_daily_pipeline
from app.ppi.run_health import compute_run_health, render_run_health_markdown

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
    # LOCKED never created/touched a JobRun row (see run_daily_pipeline's docstring), so there is
    # no run to report health for. Every other status -- including an early strict-mode refusal --
    # has a job_run_id.
    job_run_id = result.get("job_run_id")
    if job_run_id is not None:
        with get_session() as health_session:
            health = compute_run_health(health_session, job_run_id)
        result["run_health"] = health.as_dict()
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(render_run_health_markdown(health))

    print(json.dumps(result, indent=2, default=str))
    # LOCKED is benign: another run is genuinely in progress (the filesystem lock backstopping
    # the GitHub Actions concurrency group). Exit 0 so it never trips a failure notification.
    raise SystemExit(0 if result.get("status") in {"OK", "PARTIAL", "ALREADY_COMPLETE", "LOCKED"} else 1)
