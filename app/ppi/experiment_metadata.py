"""Append-only first-eligible-observation marker for the preregistered Qwen-vs-DeepSeek
matched-model comparison (``docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md``).

Records, once and only once per ``experiment_key``, the first canonical JobRun that produced at
least one valid matched pair. Never edited afterward -- a second call for an already-recorded
``experiment_key`` is a no-op, by design, so the pipeline can call this unconditionally every run
without risking a retroactive rewrite of "observation #1".
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExperimentMetadata

# Identifies this exact preregistered experiment -- the frozen Qwen V1 vs. DeepSeek V4 Flash 0731
# (reasoning disabled, identical V1 prompt) matched comparison. A future methodology change to
# either arm's frozen configuration would, per the preregistration's own amendment rules, start a
# new, separately versioned series -- which would use a new experiment_key, not reuse this one.
QWEN_VS_DEEPSEEK_EXPERIMENT_KEY = "qwen_v1_vs_deepseek_v4_flash_0731_v1"

# The commit that merged docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md into main.
PREREGISTRATION_COMMIT_SHA = "b7b0b03e7410f574dd7ee161a13c6d375bd84a10"


def record_first_eligible_observation(
    session: Session,
    *,
    job_run_id: int,
    observed_at: datetime,
    experiment_key: str = QWEN_VS_DEEPSEEK_EXPERIMENT_KEY,
) -> ExperimentMetadata:
    """Writes the marker row if (and only if) one does not already exist for ``experiment_key``.

    ``implementation_commit_sha`` is read from the ``GITHUB_SHA`` environment variable that
    GitHub Actions sets automatically for every run -- the exact commit of this repository that
    was actually checked out and executing when the first eligible observation occurred, with no
    manual bookkeeping required. Falls back to ``None`` outside of Actions (e.g. local testing).
    """
    existing = session.scalar(
        select(ExperimentMetadata).where(ExperimentMetadata.experiment_key == experiment_key)
    )
    if existing is not None:
        return existing

    row = ExperimentMetadata(
        experiment_key=experiment_key,
        preregistration_commit_sha=PREREGISTRATION_COMMIT_SHA,
        implementation_commit_sha=os.environ.get("GITHUB_SHA"),
        first_job_run_id=job_run_id,
        first_observed_at=observed_at,
    )
    session.add(row)
    session.flush()
    return row
