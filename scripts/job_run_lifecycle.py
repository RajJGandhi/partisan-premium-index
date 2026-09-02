"""CLI wrapper the workflow uses to open and finalize the one canonical ``job_runs`` record.

Used by ``.github/workflows/ppi-daily.yml``:

    # right after runtime setup, before migrations / provider check / the pipeline
    python scripts/job_run_lifecycle.py start \
        --trigger "$SLOT" --strict-llm-only \
        --workflow-run-id "$RUN_ID/$RUN_ATTEMPT" --git-sha "$SHA"

    # last step, if: always()
    python scripts/job_run_lifecycle.py finalize \
        --run-key "$RUN_KEY" --workflow-conclusion "${{ job.status }}" \
        --error-stage "$STAGE"

``start`` prints ``run_key`` / ``job_run_id`` to ``$GITHUB_OUTPUT`` so ``finalize`` (and
diagnostics) can reference the exact row. Both subcommands exit 0 on any *operational* problem --
``finalize`` in particular is an ``if: always()`` backstop and must never itself fail the job --
but ``start`` exits non-zero if it genuinely cannot open a record, since that means observability
is broken for this run.

Nothing here prints a secret: it echoes only ``run_key`` / ``job_run_id`` / ``status`` / a coarse
outcome slug.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from sqlalchemy.orm import Session

from app.db.retry import run_in_session
from app.ppi.job_run_lifecycle import (
    derive_run_key,
    ensure_lifecycle_columns,
    finalize_run,
    open_run,
    run_status_summary,
)


def _github_output(**pairs: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in pairs.items():
            fh.write(f"{key}={value}\n")


def _resolve_run_key(args: argparse.Namespace) -> str:
    if getattr(args, "run_key", None):
        return str(args.run_key)
    run_date = date.fromisoformat(args.run_date) if getattr(args, "run_date", None) else None
    return derive_run_key(args.trigger, run_date)


def cmd_start(args: argparse.Namespace) -> int:
    ensure_lifecycle_columns()
    run_key = _resolve_run_key(args)
    pipeline_mode = "strict_llm_only" if args.strict_llm_only else "standard_mixed_fallback_allowed"

    def _open(session: Session) -> dict[str, object]:
        job, outcome = open_run(
            session,
            run_key=run_key,
            trigger_type=args.trigger,
            pipeline_mode=pipeline_mode,
            workflow_run_id=args.workflow_run_id or None,
            git_sha=args.git_sha or None,
            force=args.force,
        )
        return {"run_key": job.run_key, "job_run_id": job.id, "outcome": outcome, "status": job.status}

    payload = {"command": "start", **run_in_session(_open, description="open canonical run record")}
    _github_output(run_key=payload["run_key"], job_run_id=payload["job_run_id"], outcome=payload["outcome"])
    print(json.dumps(payload, indent=2))
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    try:
        ensure_lifecycle_columns()
        run_key = _resolve_run_key(args)
        # A push / re-export-only run has no pipeline attempt to finalize.
        if run_key.rsplit(":", 1)[-1] in {"none", ""}:
            print(json.dumps({"command": "finalize", "action": "noop_not_a_pipeline_run", "run_key": run_key}))
            return 0
        result = run_in_session(
            lambda session: finalize_run(
                session,
                run_key=run_key,
                workflow_conclusion=args.workflow_conclusion,
                error_stage=args.error_stage or None,
                workflow_run_id=args.workflow_run_id or None,
                git_sha=args.git_sha or None,
            ),
            description="finalize canonical run record",
        )
        result["command"] = "finalize"
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:  # never fail the always() step
        print(f"::warning::job_run_lifecycle finalize could not update the record: {type(exc).__name__}: {exc}")
        return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Print the DB-derived run-health block (same one the public export embeds). Handy for
    operators and for the failure-alerting check."""
    from app.db.database import db_diagnostics

    summary = run_in_session(run_status_summary, description="run-health summary")
    summary["db"] = db_diagnostics()
    print(json.dumps(summary, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open / finalize the canonical PPI run record.")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Open (or re-attach to) the RUNNING run record.")
    start.add_argument("--trigger", required=True, help="primary | backup | adhoc")
    start.add_argument("--run-date", default=None, help="YYYY-MM-DD (default: today, UTC)")
    start.add_argument("--strict-llm-only", action="store_true")
    start.add_argument("--workflow-run-id", default="")
    start.add_argument("--git-sha", default="")
    start.add_argument("--force", action="store_true")
    start.set_defaults(func=cmd_start)

    fin = sub.add_parser("finalize", help="Guarantee a terminal status for the run record.")
    fin.add_argument("--run-key", default=None, help="Exact run_key (preferred; from `start` output)")
    fin.add_argument("--trigger", default=None, help="Used to derive run_key when --run-key is absent")
    fin.add_argument("--run-date", default=None)
    fin.add_argument("--workflow-conclusion", required=True, help="GitHub ${{ job.status }}")
    fin.add_argument("--error-stage", default="")
    fin.add_argument("--workflow-run-id", default="")
    fin.add_argument("--git-sha", default="")
    fin.set_defaults(func=cmd_finalize)

    summ = sub.add_parser("summary", help="Print the DB-derived run-health block.")
    summ.set_defaults(func=cmd_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
