from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import JobRun, Market, MarketSnapshot
from app.ppi.pipeline import _upsert_daily_snapshot

BOOK = {"yes_best_bid": 0.4, "yes_best_ask": 0.42, "yes_midpoint": 0.41, "spread": 0.02}


def _setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


def _job(session, run_key: str, trigger_type: str) -> JobRun:
    job = JobRun(run_key=run_key, job_name="daily_pipeline", trigger_type=trigger_type, status="RUNNING")
    session.add(job)
    session.flush()
    return job


def test_same_run_key_reuses_the_same_snapshot_row(tmp_path):
    """A retry of the same canonical run must not create a second historical observation."""
    Session = _setup(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Test?", enabled=True)
        session.add(market)
        session.flush()
        job = _job(session, "ppi-daily:2026-09-03:primary", "primary")

        a = _upsert_daily_snapshot(session, market, BOOK, [], [], "OK", "first", False, run_key=job.run_key, job=job)
        b = _upsert_daily_snapshot(session, market, BOOK, [], [], "OK", "retry", False, run_key=job.run_key, job=job)

        assert a.id == b.id
        assert b.status_message == "retry"
        assert session.query(MarketSnapshot).filter_by(snapshot_kind="daily").count() == 1


def test_different_run_key_same_day_creates_a_second_observation(tmp_path):
    """The 21:00 (backup) run must not overwrite the 09:00 (primary) run's snapshot."""
    Session = _setup(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Test?", enabled=True)
        session.add(market)
        session.flush()

        am_job = _job(session, "ppi-daily:2026-09-03:primary", "primary")
        am = _upsert_daily_snapshot(session, market, {"yes_midpoint": 0.61}, [], [], "OK", "AM", False,
                                    run_key=am_job.run_key, job=am_job)
        am_id = am.id

        pm_job = _job(session, "ppi-daily:2026-09-03:backup", "backup")
        pm = _upsert_daily_snapshot(session, market, {"yes_midpoint": 0.65}, [], [], "OK", "PM", False,
                                    run_key=pm_job.run_key, job=pm_job)

        assert pm.id != am_id
        rows = session.query(MarketSnapshot).filter_by(market_id=market.id, snapshot_kind="daily").order_by(
            MarketSnapshot.id
        ).all()
        assert len(rows) == 2
        assert [r.run_key for r in rows] == ["ppi-daily:2026-09-03:primary", "ppi-daily:2026-09-03:backup"]
        assert [r.trigger_type for r in rows] == ["primary", "backup"]
        # the AM observation still exists, unmodified
        assert session.get(MarketSnapshot, am_id).status_message == "AM"
        assert session.get(MarketSnapshot, am_id).yes_midpoint == 0.61
