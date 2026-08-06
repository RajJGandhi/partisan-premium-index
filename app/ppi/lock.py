"""Filesystem-based concurrency lock for the PPI pipeline.

Defense in depth alongside the GitHub Actions ``concurrency:`` group on the scheduled workflow:
that group protects against two *workflow* invocations overlapping, but not against an
out-of-band invocation (a manual `python scripts/run_ppi_daily.py`, a stray cron entry, a second
self-hosted runner) racing a scheduled run on the same machine. This lock is the backstop for
that case.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_LOCK_PATH = Path("data/.ppi_pipeline.lock")


class PipelineLockedError(RuntimeError):
    """Raised when another process already holds the pipeline lock."""


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists, just owned by someone else
    return True


def _read_holder_pid(lock_path: Path) -> int | None:
    try:
        first_line = lock_path.read_text().splitlines()[0]
        return int(first_line)
    except (OSError, IndexError, ValueError):
        return None


def _try_acquire(lock_path: Path) -> bool:
    """Atomically create the lock file; False if it already exists (no TOCTOU race)."""
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()}\n{time.time()}\n")
    return True


@contextmanager
def pipeline_lock(lock_path: Path = DEFAULT_LOCK_PATH) -> Iterator[None]:
    """Hold an exclusive, PID-verified lock for the duration of one pipeline run.

    A lock file left behind by a process that is no longer running (a stale lock, e.g. after a
    hard crash or a killed runner) is automatically reclaimed. A lock held by a still-running
    process raises ``PipelineLockedError`` immediately -- this must never block/wait, since a
    scheduled run that can't proceed should fail fast and be visible, not hang.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not _try_acquire(lock_path):
        holder_pid = _read_holder_pid(lock_path)
        if holder_pid is not None and _pid_is_running(holder_pid):
            raise PipelineLockedError(
                f"Another pipeline run (pid {holder_pid}) holds {lock_path}; refusing to start a second run."
            )
        # Stale lock: reclaim and retry once.
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        if not _try_acquire(lock_path):
            holder_pid = _read_holder_pid(lock_path)
            raise PipelineLockedError(
                f"Pipeline lock {lock_path} is held (pid {holder_pid}); refusing to start a second run."
            )
    try:
        yield
    finally:
        if _read_holder_pid(lock_path) == os.getpid():
            try:
                lock_path.unlink()
            except OSError:
                pass
