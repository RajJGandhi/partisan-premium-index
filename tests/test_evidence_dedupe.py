from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import EvidenceItem, Market
from app.ppi.evidence import EvidenceCandidate, insert_and_classify_candidate


def test_evidence_deduplicates_by_market_and_content_hash(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    with Session.begin() as session:
        market = Market(
            platform_market_id="1",
            tracking_id="T-1",
            question="Will the Democrats win the Maine Senate race in 2026?",
            enabled=True,
        )
        session.add(market)
        session.flush()
        candidate = EvidenceCandidate(
            "manual",
            "Test",
            "New Maine Senate poll released",
            "https://example.com/a?utm_source=x",
            content_text="polling",
        )
        first, inserted1 = insert_and_classify_candidate(session, market, None, candidate)
        second, inserted2 = insert_and_classify_candidate(session, market, None, candidate)
        assert inserted1 is True
        assert inserted2 is False
        assert first.id == second.id
        assert session.query(EvidenceItem).count() == 1
