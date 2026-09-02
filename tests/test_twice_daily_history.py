"""Acceptance test for run-aware historical persistence.

A twice-daily canonical schedule (09:00 / 21:00 America/Toronto == ~13:00 / ~01:00 UTC) produces
two runs on the same calendar date. Before this change, ``market_snapshots`` and ``daily_index``
used date-level uniqueness, so the 21:00 run silently overwrote the 09:00 one -- the product
claimed a twice-daily series while parts of the history were effectively once daily.

These tests drive the real ``run_daily_pipeline`` code path (reusing the dual-series harness),
with ``utcnow`` pinned to each canonical run hour so the two runs land in distinct slots exactly
as the scheduled workflow does, and assert both observations survive, retries stay idempotent,
and ``llm_forecasts`` / ``blind_index_runs`` still append.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.db.models import BlindIndexRun, DailyIndex, LLMForecast, Market, MarketSnapshot
from tests.test_dual_series_pipeline import _FakePolymarketClient, _setup

RUN_DATE = date(2026, 9, 3)
AM_NOW = datetime(2026, 9, 3, 13, 0, tzinfo=UTC)  # ~09:00 America/Toronto -> "primary" slot
PM_NOW = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)  # ~21:00 America/Toronto -> "backup" slot
N_MARKETS = 2
PROVIDERS = 2  # ollama (Qwen comparison) + openrouter (DeepSeek primary)


class _PerMarketFakeClient(_FakePolymarketClient):
    """Per-market id so >1 market can be tracked without colliding on platform_market_id."""

    def fetch_market(self, market):
        return {"id": market.platform_market_id, "question": market.question}, "https://fake/m", 200


def _make_markets(session, n: int) -> None:
    for i in range(1, n + 1):
        session.add(
            Market(
                platform_market_id=str(i),
                tracking_id=f"T-{i}",
                question=f"Will market {i} resolve YES?",
                rules="Resolves YES if the test passes.",
                enabled=True,
                active=True,
                closed=False,
            )
        )
    session.flush()


def _counts(session) -> dict[str, int]:
    return {
        "daily_snapshots": session.scalar(
            select(func.count()).select_from(MarketSnapshot).where(MarketSnapshot.snapshot_kind == "daily")
        ),
        "index": session.scalar(select(func.count()).select_from(DailyIndex)),
        "forecasts": session.scalar(select(func.count()).select_from(LLMForecast)),
        "blind_index": session.scalar(select(func.count()).select_from(BlindIndexRun)),
    }


def _run(pipeline_module, monkeypatch, trigger: str, now: datetime, tmp_path, *, force: bool = False):
    monkeypatch.setattr(pipeline_module, "utcnow", lambda: now)
    return pipeline_module.run_daily_pipeline(
        trigger, run_date=RUN_DATE, strict_llm_only=True, force=force, lock_path=tmp_path / "p.lock"
    )


def test_two_runs_same_date_produce_two_observations_each(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline_module, "TrackedPolymarketClient", _PerMarketFakeClient)
    with Session.begin() as session:
        _make_markets(session, N_MARKETS)

    # --- 09:00 primary run ---
    r1 = _run(pipeline_module, monkeypatch, "primary", AM_NOW, tmp_path)
    assert r1["status"] == "OK"
    with Session() as session:
        c1 = _counts(session)
    assert c1 == {
        "daily_snapshots": N_MARKETS,
        "index": 1,
        "forecasts": N_MARKETS * PROVIDERS,
        "blind_index": 1,
    }

    # capture the AM observation so we can prove the PM run does not mutate it
    with Session() as session:
        am_index = session.scalar(select(DailyIndex))
        am_index_id, am_index_ts = am_index.id, am_index.generated_at
        am_index_premium, am_index_run_key = am_index.average_signed_premium, am_index.run_key
        am_snaps = {s.market_id: (s.id, s.comparison_price, s.run_key) for s in session.scalars(select(MarketSnapshot))}

    # --- 21:00 backup run, SAME date ---
    r2 = _run(pipeline_module, monkeypatch, "backup", PM_NOW, tmp_path)
    assert r2["status"] == "OK"
    assert r2["run_key"] != r1["run_key"]

    with Session() as session:
        c2 = _counts(session)
        # +N snapshots, +1 index, +2N forecasts, +1 blind index -- nothing overwritten
        assert c2 == {
            "daily_snapshots": 2 * N_MARKETS,
            "index": 2,
            "forecasts": 2 * N_MARKETS * PROVIDERS,
            "blind_index": 2,
        }

        # two index rows, same semantic date, distinct run identity
        index_rows = session.scalars(select(DailyIndex).order_by(DailyIndex.id)).all()
        assert [r.index_date for r in index_rows] == [RUN_DATE, RUN_DATE]
        assert [r.trigger_type for r in index_rows] == ["primary", "backup"]
        assert index_rows[0].run_key.endswith(":primary")
        assert index_rows[1].run_key.endswith(":backup")

        # the AM index row is intact
        am_after = session.get(DailyIndex, am_index_id)
        assert (am_after.generated_at, am_after.average_signed_premium, am_after.run_key) == (
            am_index_ts,
            am_index_premium,
            am_index_run_key,
        )
        assert am_after.trigger_type == "primary"

        # every AM snapshot still exists with its original id/price/run_key
        for sid, price, run_key in am_snaps.values():
            row = session.get(MarketSnapshot, sid)
            assert row is not None and row.comparison_price == price
            assert row.run_key == run_key and run_key.endswith(":primary")

        # each market now has exactly two daily snapshots on RUN_DATE: one primary, one backup
        for i in range(1, N_MARKETS + 1):
            rows = session.scalars(
                select(MarketSnapshot)
                .where(MarketSnapshot.market_id == i, MarketSnapshot.snapshot_kind == "daily")
                .order_by(MarketSnapshot.id)
            ).all()
            assert [r.trigger_type for r in rows] == ["primary", "backup"]
            assert all(r.snapshot_date == RUN_DATE for r in rows)
            assert rows[0].run_key != rows[1].run_key


def test_retry_of_the_same_run_key_adds_no_new_history(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline_module, "TrackedPolymarketClient", _PerMarketFakeClient)
    with Session.begin() as session:
        _make_markets(session, 1)

    _run(pipeline_module, monkeypatch, "primary", AM_NOW, tmp_path)
    with Session() as session:
        before = _counts(session)

    # same slot + same date == same run_key; force a full re-run
    retry = _run(pipeline_module, monkeypatch, "primary", AM_NOW, tmp_path, force=True)
    assert retry["status"] == "OK"
    with Session() as session:
        after = _counts(session)
    assert after == before, f"retry created duplicate history: {before} -> {after}"

    # a different slot on the same date DOES add a new observation
    _run(pipeline_module, monkeypatch, "backup", PM_NOW, tmp_path)
    with Session() as session:
        final = _counts(session)
    assert final["daily_snapshots"] == before["daily_snapshots"] * 2
    assert final["index"] == before["index"] + 1
    assert final["forecasts"] == before["forecasts"] * 2
