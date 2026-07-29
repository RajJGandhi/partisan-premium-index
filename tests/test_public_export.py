from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    DailyIndex,
    EvidenceItem,
    FairValue,
    FairValueComponent,
    FairValueRevision,
    JobRun,
    Market,
    MarketResolution,
    MarketSnapshot,
    MarketSource,
    Prediction,
    SourceRun,
)
from scripts.export_public_bundle import build_public_bundle, write_public_bundle


def test_public_export_is_sanitized_and_complete(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 29, 15, 35, tzinfo=timezone.utc)

    with Session.begin() as session:
        market = Market(
            platform="polymarket",
            platform_market_id="market-1",
            tracking_id="RSO-0001",
            slug="test-market",
            question="Will the test outcome happen?",
            description="Public description",
            rules="Public rules",
            resolution_source="Official source",
            category="politics",
            region="US",
            enabled=True,
            current_thesis="Public thesis",
        )
        session.add(market)
        session.flush()

        session.add(
            MarketSnapshot(
                market_id=market.id,
                timestamp=now,
                snapshot_date=date(2026, 7, 29),
                snapshot_kind="daily",
                comparison_price=0.60,
                yes_best_bid=0.59,
                yes_best_ask=0.61,
                spread=0.02,
                volume=1_000,
                liquidity=500,
                fair_value=0.55,
                partisan_premium=0.05,
                freshness_status="FRESH",
                pipeline_status="OK",
                raw_orderbook_json='{"secret": "never export"}',
            )
        )
        fair_value = FairValue(
            market_id=market.id,
            published_fair_yes=0.55,
            last_published_at=now,
            source_notes="private analyst note",
        )
        session.add(fair_value)
        session.add(
            FairValueComponent(
                market_id=market.id,
                component_type="polling",
                probability=0.54,
                weight=0.35,
                source_label="Public poll",
                source_url="https://example.com/poll#fragment",
                observed_at=now,
                notes="private component note",
            )
        )
        session.add(
            MarketSource(
                market_id=market.id,
                source_type="rss",
                name="Public feed",
                url="https://example.com/feed.xml",
                query="private search query",
                config_json='{"token": "private"}',
            )
        )

        accepted = EvidenceItem(
            market_id=market.id,
            source_type="rss",
            source_name="Example",
            title="Material public evidence",
            canonical_url="https://example.com/story#section",
            published_at=now,
            discovered_at=now,
            content_text="full copyrighted article body",
            normalized_title="material public evidence",
            content_hash="a" * 64,
            raw_json='{"private": true}',
            relevant=True,
            relevance_score=0.9,
            source_quality=0.8,
            changes_probability=True,
            direction="YES",
            estimated_magnitude=0.03,
            category="polling",
            summary="A safe public summary.",
            reason="private classifier reasoning",
            classifier_raw_json='{"private": true}',
            review_status="AUTO_ACCEPTED",
        )
        rejected = EvidenceItem(
            market_id=market.id,
            source_type="rss",
            title="Rejected evidence",
            canonical_url="https://example.com/rejected",
            discovered_at=now,
            normalized_title="rejected evidence",
            content_hash="b" * 64,
            relevant=True,
            summary="Should not be public",
            review_status="REJECTED",
        )
        unsafe = EvidenceItem(
            market_id=market.id,
            source_type="manual",
            title="Unsafe internal URL",
            canonical_url="http://localhost:8000/internal",
            discovered_at=now,
            normalized_title="unsafe internal url",
            content_hash="c" * 64,
            relevant=True,
            summary="Should not be public",
            review_status="APPROVED",
        )
        session.add_all([accepted, rejected, unsafe])
        session.flush()

        revision = FairValueRevision(
            market_id=market.id,
            revision_number=1,
            fair_value=0.55,
            previous_fair_value=None,
            components_json=json.dumps({"polling": {"probability": 0.54, "notes": "private nested note"}}),
            weights_json=json.dumps({"polling": 0.35}),
            effective_weights_json=json.dumps({"polling": 1.0}),
            evidence_ids_json=json.dumps([accepted.id, rejected.id]),
            thesis="Public thesis",
            justification="private approval justification",
            published_at=now,
            published_by="private-admin-name",
        )
        session.add(revision)
        session.flush()

        prediction = Prediction(
            market_id=market.id,
            initial_revision_id=revision.id,
            initial_publication_at=now,
            initial_fair_value=0.55,
            initial_market_probability=0.60,
            initial_thesis="Public thesis",
            status="RESOLVED",
            final_outcome=1.0,
            resolved_at=now,
            ppi_brier_score=0.2025,
            market_brier_score=0.16,
            performance_difference=-0.0425,
        )
        session.add(prediction)
        session.add(
            MarketResolution(
                market_id=market.id,
                resolved_outcome=1.0,
                resolved_label="YES",
                resolved_at=now,
                source_url="https://example.com/resolution",
                notes="private resolution note",
                recorded_by="private-admin-name",
            )
        )
        session.add(
            DailyIndex(
                index_date=date(2026, 7, 29),
                tracked_market_count=1,
                fresh_market_count=1,
                average_signed_premium=0.05,
                average_absolute_premium=0.05,
                share_above_fair_value=1.0,
                generated_at=now,
                status="OK",
            )
        )
        job = JobRun(
            run_key="ppi-daily:2026-07-29:test",
            job_name="ppi-daily",
            trigger_type="test",
            started_at=now,
            finished_at=now,
            status="OK",
            markets_attempted=1,
            markets_succeeded=1,
            evidence_discovered=3,
            evidence_relevant=1,
            snapshots_written=1,
            sanitized_error="private stack trace that should not export",
        )
        session.add(job)
        session.flush()
        session.add(
            SourceRun(
                job_run_id=job.id,
                market_id=market.id,
                source_name="Example feed",
                started_at=now,
                finished_at=now,
                status="OK",
                items_discovered=3,
                items_inserted=1,
                sanitized_error="private source failure details",
            )
        )

    with Session() as session:
        bundle = build_public_bundle(session, generated_at=now)

    detail = bundle["market_details"]["test-market"]
    assert detail["market"]["partisan_premium"] == 0.05
    assert detail["components"][0]["source_url"] == "https://example.com/poll"
    assert detail["evidence"] == [
        {
            "title": "Material public evidence",
            "url": "https://example.com/story",
            "source_type": "rss",
            "source_name": "Example",
            "published_at": "2026-07-29T15:35:00Z",
            "discovered_at": "2026-07-29T15:35:00Z",
            "category": "polling",
            "direction": "YES",
            "relevance_score": 0.9,
            "source_quality": 0.8,
            "estimated_magnitude": 0.03,
            "summary": "A safe public summary.",
        }
    ]
    assert detail["revisions"][0]["evidence_urls"] == ["https://example.com/story"]

    serialized = json.dumps(bundle)
    for forbidden in (
        "private analyst note",
        "private component note",
        "private search query",
        "full copyrighted article body",
        "private classifier reasoning",
        "private approval justification",
        "private nested note",
        "private-admin-name",
        "private stack trace",
        "private source failure details",
        "never export",
        "Rejected evidence",
        "Unsafe internal URL",
    ):
        assert forbidden not in serialized

    output_dir = tmp_path / "web" / "public" / "data"
    stale_file = output_dir / "markets" / "stale-market.json"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("{}", encoding="utf-8")

    written = write_public_bundle(bundle, output_dir)
    assert len(written) == 6
    assert not stale_file.exists()
    assert (output_dir / "overview.json").exists()
    assert (output_dir / "markets.json").exists()
    assert (output_dir / "track-record.json").exists()
    assert (output_dir / "system-status.json").exists()
    assert (output_dir / "manifest.json").exists()
    assert (output_dir / "markets" / "test-market.json").exists()


def test_public_export_handles_empty_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    now = datetime(2026, 7, 29, 15, 35, tzinfo=timezone.utc)

    with Session() as session:
        bundle = build_public_bundle(session, generated_at=now)

    assert bundle["markets"]["markets"] == []
    assert bundle["overview"]["coverage"]["tracked_markets"] == 0
    assert bundle["overview"]["latest_run"] is None
    assert bundle["track_record"]["summary"]["resolved_predictions"] == 0
    assert bundle["system_status"]["status"] == "NO_RUNS"
