from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import EvidenceItem, LLMForecast, Market
from app.ppi.llm_forecast_view import (
    EXPORT_COLUMNS,
    FRESHNESS_ERROR,
    FRESHNESS_MISSING,
    FRESHNESS_OK,
    FRESHNESS_SKIPPED,
    FRESHNESS_STALE,
    approx_confidence_interval,
    build_forecast_rows,
    classify_forecast_freshness,
    evidence_items_for_forecast,
    forecast_rows_to_export_dataframe,
    latest_forecast_by_market,
)


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'view.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _forecast(market_id: int, *, status="OK", generated_at=None, **kwargs) -> LLMForecast:
    defaults = dict(
        market_id=market_id,
        run_slot="2026-08-05:primary",
        run_key="run-1",
        trigger_type="manual",
        generated_at=generated_at or datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        model_provider="ollama",
        model_name="qwen3:8b",
        prompt_version="fair_value_v0.1",
        prompt_hash="hash",
        status=status,
    )
    defaults.update(kwargs)
    return LLMForecast(**defaults)


def test_approx_confidence_interval_shrinks_toward_zero_width_as_confidence_rises():
    lo, hi = approx_confidence_interval(0.5, confidence=1.0)
    assert lo == pytest.approx(0.5)
    assert hi == pytest.approx(0.5)

    lo, hi = approx_confidence_interval(0.5, confidence=0.5)
    assert lo == pytest.approx(0.25)
    assert hi == pytest.approx(0.75)


def test_approx_confidence_interval_clamps_to_zero_one():
    lo, hi = approx_confidence_interval(0.05, confidence=0.0)
    assert lo == 0.0
    lo, hi = approx_confidence_interval(0.97, confidence=0.0)
    assert hi == 1.0


@pytest.mark.parametrize(
    "status,age_hours,expected",
    [
        ("OK", 1, FRESHNESS_OK),
        ("OK", 40, FRESHNESS_STALE),
        ("ABSTAINED", 1, FRESHNESS_OK),
        ("FAILED", 1, FRESHNESS_ERROR),
        ("SKIPPED_PROVIDER", 1, FRESHNESS_SKIPPED),
    ],
)
def test_classify_forecast_freshness_states(status, age_hours, expected):
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    latest = _forecast(1, status=status, generated_at=now - timedelta(hours=age_hours))
    assert classify_forecast_freshness(latest, now=now, stale_hours=30.0) == expected


def test_classify_forecast_freshness_missing_when_no_row():
    assert classify_forecast_freshness(None) == FRESHNESS_MISSING


def test_latest_forecast_by_market_picks_most_recent_per_market():
    older = _forecast(1, generated_at=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc))
    newer = _forecast(1, generated_at=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc))
    other_market = _forecast(2, generated_at=datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc))

    latest = latest_forecast_by_market([older, newer, other_market])

    assert latest[1] is newer
    assert latest[2] is other_market


def test_evidence_items_for_forecast_preserves_order_and_skips_missing(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Q?", enabled=True)
        session.add(market)
        session.flush()
        item_a = EvidenceItem(
            market_id=market.id,
            source_type="rss",
            source_name="A",
            title="Item A",
            normalized_title="item a",
            content_hash="hash-a",
            relevant=True,
        )
        item_b = EvidenceItem(
            market_id=market.id,
            source_type="rss",
            source_name="B",
            title="Item B",
            normalized_title="item b",
            content_hash="hash-b",
            relevant=True,
        )
        session.add_all([item_a, item_b])
        session.flush()

        forecast = _forecast(market.id)
        import json

        forecast.evidence_ids_json = json.dumps([item_b.id, item_a.id, 999999])
        session.add(forecast)
        session.flush()

        result = evidence_items_for_forecast(session, forecast)

    assert [r.title for r in result] == ["Item B", "Item A"]


def test_evidence_items_for_forecast_handles_malformed_json():
    forecast = _forecast(1)
    forecast.evidence_ids_json = "not json"
    assert evidence_items_for_forecast(None, forecast) == []  # short-circuits before querying


def test_build_forecast_rows_hides_price_and_ppi_until_joined(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Will it happen?", enabled=True)
        session.add(market)
        session.flush()

        unjoined = _forecast(market.id, fair_value=0.6, confidence=0.5, run_slot="2026-08-05:primary")
        joined = _forecast(
            market.id,
            fair_value=0.6,
            confidence=0.5,
            run_slot="2026-08-05:backup",
            comparison_price_at_join=0.75,
            raw_ppi=0.15,
            joined_at=datetime(2026, 8, 5, 18, 5, tzinfo=timezone.utc),
        )
        session.add_all([unjoined, joined])
        session.flush()

        rows = build_forecast_rows([unjoined, joined], {market.id: market})

    by_slot = {r.run_slot: r for r in rows}
    assert by_slot["2026-08-05:primary"].comparison_price_at_join is None
    assert by_slot["2026-08-05:primary"].raw_ppi is None
    assert by_slot["2026-08-05:primary"].joined_at is None

    assert by_slot["2026-08-05:backup"].comparison_price_at_join == pytest.approx(0.75)
    assert by_slot["2026-08-05:backup"].raw_ppi == pytest.approx(0.15)
    assert by_slot["2026-08-05:backup"].joined_at is not None

    for row in rows:
        assert row.interval_low is not None and row.interval_high is not None
        assert row.market_label.startswith("T-1")


def test_export_dataframe_has_stable_columns_and_matches_row_count(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Q?", enabled=True)
        session.add(market)
        session.flush()
        f1 = _forecast(market.id, fair_value=0.3, confidence=0.4)
        f2 = _forecast(market.id, fair_value=0.7, confidence=0.9, run_slot="2026-08-05:backup")
        session.add_all([f1, f2])
        session.flush()
        rows = build_forecast_rows([f1, f2], {market.id: market})

    df = forecast_rows_to_export_dataframe(rows)
    assert list(df.columns) == EXPORT_COLUMNS
    assert len(df) == 2
    assert set(df["fair_value"]) == {0.3, 0.7}


def test_export_dataframe_empty_when_no_rows():
    df = forecast_rows_to_export_dataframe([])
    assert list(df.columns) == EXPORT_COLUMNS
    assert df.empty
