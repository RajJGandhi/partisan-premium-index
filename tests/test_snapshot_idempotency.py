from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Market, MarketSnapshot
from app.ppi.pipeline import _upsert_daily_snapshot


def test_daily_snapshot_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Test?", enabled=True)
        session.add(market)
        session.flush()
        book = {"yes_best_bid": 0.4, "yes_best_ask": 0.42, "yes_midpoint": 0.41, "spread": 0.02}
        a = _upsert_daily_snapshot(
            session, market, book, [], [], "OK", "No material new evidence. Published fair value unchanged.", False
        )
        b = _upsert_daily_snapshot(session, market, book, [], [], "OK", "rerun", False)
        assert a.id == b.id
        assert session.query(MarketSnapshot).filter_by(snapshot_kind="daily").count() == 1
