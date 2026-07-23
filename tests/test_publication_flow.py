import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import FairValueProposal, FairValueRevision, Market, Prediction
from app.ppi.publication import publish_proposal


def test_administrative_approval_creates_immutable_revision_and_prediction(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'approval.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Test market", enabled=True)
        session.add(market)
        session.flush()
        proposal = FairValueProposal(
            market_id=market.id,
            proposed_fair_value=0.55,
            current_published_fair_value=None,
            proposed_components_json=json.dumps({"polling": {"probability": 0.55}}),
            proposed_weights_json=json.dumps({"polling": 1}),
            effective_weights_json=json.dumps({"polling": 1}),
            evidence_ids_json="[]",
            rationale="Initial publication",
        )
        session.add(proposal)
        session.flush()
        revision = publish_proposal(
            session, proposal, thesis="Independent estimate", justification="Reviewed", reviewer="test-admin"
        )
        assert revision.revision_number == 1
        assert proposal.status == "APPROVED"
        assert session.scalar(select(FairValueRevision).where(FairValueRevision.market_id == market.id)) is not None
        prediction = session.scalar(select(Prediction).where(Prediction.market_id == market.id))
        assert prediction.initial_fair_value == 0.55
