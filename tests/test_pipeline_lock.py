from __future__ import annotations

import os

import pytest

from app.ppi.lock import PipelineLockedError, pipeline_lock


def test_lock_is_acquired_and_released(tmp_path):
    lock_path = tmp_path / "pipeline.lock"
    with pipeline_lock(lock_path):
        assert lock_path.exists()
        assert lock_path.read_text().splitlines()[0] == str(os.getpid())
    assert not lock_path.exists()


def test_lock_released_even_on_exception(tmp_path):
    lock_path = tmp_path / "pipeline.lock"
    with pytest.raises(ValueError):
        with pipeline_lock(lock_path):
            assert lock_path.exists()
            raise ValueError("boom")
    assert not lock_path.exists()


def test_concurrent_acquire_by_a_running_process_is_refused(tmp_path):
    lock_path = tmp_path / "pipeline.lock"
    # Write a lock file claiming to be held by this test process itself -- guaranteed "running".
    lock_path.write_text(f"{os.getpid()}\n0\n")
    with pytest.raises(PipelineLockedError, match="refusing to start a second run"):
        with pipeline_lock(lock_path):
            pass
    # A refused acquisition must never delete the other holder's lock.
    assert lock_path.exists()


def test_stale_lock_from_a_dead_process_is_reclaimed(tmp_path):
    lock_path = tmp_path / "pipeline.lock"
    # PID 1 belongs to init/launchd, never this test process, but more importantly we pick a PID
    # that is astronomically unlikely to be alive: reuse a fresh temp lock's own dead-process
    # simulation by picking a PID far outside any real process table.
    dead_pid = 999999
    lock_path.write_text(f"{dead_pid}\n0\n")
    with pipeline_lock(lock_path):
        # Successfully reclaimed and re-acquired under our own PID.
        assert lock_path.read_text().splitlines()[0] == str(os.getpid())
    assert not lock_path.exists()


def test_nested_acquire_while_held_by_self_process_still_refuses(tmp_path):
    """Even the *same* process must not run two overlapping pipeline invocations concurrently --
    the lock is not reentrant."""
    lock_path = tmp_path / "pipeline.lock"
    with pipeline_lock(lock_path):
        with pytest.raises(PipelineLockedError):
            with pipeline_lock(lock_path):
                pass
