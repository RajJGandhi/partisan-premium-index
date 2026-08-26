from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import JobRun, LLMForecast, Market
from app.ppi.public_forecast import current_public_forecast, current_public_forecasts


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'public_forecast.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _job(run_key: str, **kwargs) -> JobRun:
    defaults = dict(
        run_key=run_key,
        job_name="daily_pipeline",
        trigger_type="primary",
        status="OK",
        pipeline_mode="strict_llm_only",
        run_classification="canonical",
    )
    defaults.update(kwargs)
    return JobRun(**defaults)


def _market(session, tracking_id="T-1") -> Market:
    market = Market(platform_market_id=tracking_id, tracking_id=tracking_id, question="Will it happen?", enabled=True)
    session.add(market)
    session.flush()
    return market


def _forecast(
    job: JobRun,
    market: Market,
    *,
    status: str,
    generated_at: datetime,
    fair_value: float | None = None,
    raw_ppi: float | None = None,
    comparison_price_at_join: float | None = None,
    reviewed_status: str = "UNREVIEWED",
) -> LLMForecast:
    return LLMForecast(
        market_id=market.id,
        job_run_id=job.id,
        run_key=job.run_key,
        run_slot=f"{job.run_key}:{market.id}",
        trigger_type=job.trigger_type,
        generated_at=generated_at,
        model_provider="openrouter",  # primary series since the 2026-08-26 cutover
        model_name="deepseek/deepseek-v4-flash-0731",
        prompt_version="fair_value_v0.1",
        status=status,
        fair_value=fair_value,
        raw_ppi=raw_ppi,
        comparison_price_at_join=comparison_price_at_join,
        confidence=0.8,
        rationale="Because reasons.",
        reviewed_status=reviewed_status,
    )


def test_canonical_ok_forecast_publishes_automatically_without_review(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
                # Never reviewed at all -- must still publish. Publication is not gated on review.
                reviewed_status="UNREVIEWED",
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "OK"
        assert result.fair_value == 0.40
        assert result.market_probability == 0.45
        assert result.partisan_premium == 0.05


def test_abstained_forecast_is_shown_as_abstention_not_a_missing_value(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                job,
                market,
                status="ABSTAINED",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.5,  # the model still returns its best guess even when abstaining
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "ABSTAINED"
        # No numeric value is published for an abstention -- never invent a probability or PPI.
        assert result.fair_value is None
        assert result.market_probability is None
        assert result.partisan_premium is None


def test_failed_forecast_shows_error_never_a_fallback_value(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(job, market, status="FAILED", generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc))
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "ERROR"
        assert result.fair_value is None
        assert result.partisan_premium is None


def test_market_with_no_canonical_forecast_reports_none(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = _market(session)
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "NONE"
        assert result.fair_value is None


def test_noncanonical_run_never_used_as_the_current_public_forecast(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:adhoc", run_classification="noncanonical_mixed")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "NONE"


def test_superseded_canonical_run_is_not_used(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        superseding_job = _job("ppi-daily:2026-08-11:primary-redo")
        session.add(superseding_job)
        session.flush()
        superseded_job = _job("ppi-daily:2026-08-11:primary", superseded_by_id=superseding_job.id)
        session.add(superseded_job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                superseded_job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "NONE"


def test_flagged_forecast_is_suppressed_from_public_display(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
                reviewed_status="FLAGGED",
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.forecast_status == "FLAGGED"
        assert result.fair_value is None
        assert result.partisan_premium is None


def test_latest_of_multiple_canonical_slots_is_used(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        primary_job = _job("ppi-daily:2026-08-11:primary")
        session.add(primary_job)
        session.flush()
        backup_job = _job("ppi-daily:2026-08-11:backup")
        session.add(backup_job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                primary_job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
            )
        )
        session.add(
            _forecast(
                backup_job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc),
                fair_value=0.38,
                raw_ppi=0.09,
                comparison_price_at_join=0.47,
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.run_key == "ppi-daily:2026-08-11:backup"
        assert result.fair_value == 0.38


def test_experimental_series_never_becomes_the_public_headline_forecast(tmp_path):
    """Regression test for a real leak found while implementing the Qwen-vs-DeepSeek matched
    series: this query used to have no model_provider filter at all, so a same-cycle comparison
    forecast generated slightly after the primary one (the normal case -- see app.ppi.pipeline's
    dual-series call order) would win the generated_at DESC ordering and silently become "the"
    public forecast for that market -- exactly what must never happen. Provider roles reflect the
    2026-08-26 cutover (DeepSeek primary, Qwen comparison); the leak being guarded against is
    provider-agnostic."""
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        market = _market(session)
        session.add(
            _forecast(
                job,
                market,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, 0, tzinfo=timezone.utc),
                fair_value=0.40,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
            )
        )
        # Same job, same market, generated strictly later -- the comparison arm always runs
        # after the primary one in the real pipeline loop.
        session.add(
            LLMForecast(
                market_id=market.id,
                job_run_id=job.id,
                run_key=job.run_key,
                run_slot=f"{job.run_key}:{market.id}",
                trigger_type=job.trigger_type,
                generated_at=datetime(2026, 8, 11, 10, 0, 5, tzinfo=timezone.utc),
                model_provider="ollama",
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
                status="OK",
                fair_value=0.85,
                raw_ppi=-0.40,
                comparison_price_at_join=0.45,
                confidence=0.8,
                rationale="Qwen's rationale.",
            )
        )
        session.flush()
        market_id = market.id

    with Session() as session:
        result = current_public_forecast(session, market_id)
        assert result.fair_value == 0.40  # DeepSeek's (primary) value, never Qwen's 0.85
        assert result.run_key == "ppi-daily:2026-08-11:primary"


def test_bulk_lookup_returns_an_entry_for_every_requested_market_id(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job("ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        with_forecast = _market(session, "T-1")
        without_forecast = _market(session, "T-2")
        session.add(
            _forecast(
                job,
                with_forecast,
                status="OK",
                generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
                fair_value=0.4,
                raw_ppi=0.05,
                comparison_price_at_join=0.45,
            )
        )
        session.flush()
        ids = [with_forecast.id, without_forecast.id]

    with Session() as session:
        results = current_public_forecasts(session, ids)
        assert set(results.keys()) == set(ids)
        assert results[with_forecast.id].forecast_status == "OK"
        assert results[without_forecast.id].forecast_status == "NONE"
