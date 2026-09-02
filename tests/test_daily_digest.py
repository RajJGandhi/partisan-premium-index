from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import EvidenceItem, FairValueProposal, JobRun, Market, MarketSnapshot, SourceRun
from app.ppi.digest import build_daily_digest, write_daily_digest


def test_daily_digest_contains_required_sections(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'digest.db'}")
    Base.metadata.create_all(engine)
    day = date(2026, 7, 20)
    with Session(engine) as session:
        market = Market(
            tracking_id="DIGEST-1",
            platform="polymarket",
            platform_market_id="digest-market",
            question="Will the test candidate win?",
            enabled=True,
        )
        session.add(market)
        session.flush()
        session.add_all(
            [
                MarketSnapshot(
                    market_id=market.id,
                    timestamp=datetime(2026, 7, 19, 13, tzinfo=UTC),
                    snapshot_date=day - timedelta(days=1),
                    snapshot_kind="daily",
                    run_key="digest-run-prev",
                    comparison_price=0.50,
                    pipeline_status="OK",
                ),
                MarketSnapshot(
                    market_id=market.id,
                    timestamp=datetime(2026, 7, 20, 13, tzinfo=UTC),
                    snapshot_date=day,
                    snapshot_kind="daily",
                    run_key="digest-run",
                    comparison_price=0.54,
                    fair_value=0.49,
                    partisan_premium=0.05,
                    pipeline_status="OK",
                ),
            ]
        )
        session.add(
            EvidenceItem(
                market_id=market.id,
                source_type="manual",
                source_name="Test source",
                title="New poll",
                normalized_title="new poll",
                content_hash="digest-hash",
                discovered_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
                relevant=True,
                relevance_score=0.9,
                source_quality=0.9,
                changes_probability=True,
                direction="YES",
                category="polling",
                summary="A high-quality poll moved the average.",
            )
        )
        session.add(
            FairValueProposal(
                market_id=market.id,
                proposed_fair_value=0.52,
                current_published_fair_value=0.49,
                proposed_components_json="{}",
                proposed_weights_json="{}",
                rationale="New polling input.",
                confidence=0.8,
                created_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
            )
        )
        job = JobRun(
            run_key="digest-run",
            job_name="daily_pipeline",
            status="PARTIAL",
            markets_attempted=1,
            markets_succeeded=1,
            error_count=1,
        )
        session.add(job)
        session.flush()
        session.add(
            SourceRun(
                job_run_id=job.id,
                market_id=market.id,
                source_name="test-source",
                status="FAILED",
                sanitized_error="timeout",
            )
        )
        session.commit()

        markdown, summary = build_daily_digest(session, job, day)
        assert "Important market-price movements" in markdown
        assert "+4.0 pp" in markdown
        assert "Newly relevant evidence" in markdown
        assert "Proposed fair-value changes" in markdown
        assert "Failed or stale sources" in markdown
        assert summary["material_price_movements"] == 1
        assert summary["pending_proposals"] == 1

        result = write_daily_digest(session, job, day, tmp_path)
        assert result.dated_path.exists()
        assert result.latest_path.exists()
