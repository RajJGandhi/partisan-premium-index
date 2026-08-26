"""End-to-end tests of the dual-series (DeepSeek primary + Qwen comparison, since the
2026-08-26 cutover -- see docs/research/DEEPSEEK_PRIMARY_CUTOVER_DEVIATION_20260826.md) forecast
generation wired into app.ppi.pipeline._run_daily_pipeline_locked -- proving the actual
production code path, not just the underlying generate_blind_forecast/is_matched_pair building
blocks (covered separately in tests/test_openrouter_provider.py).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date

import pytest
import requests
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.database import Base
from app.db.models import ExperimentMetadata, JobRun, LLMForecast, Market
from app.ppi.experiment_metadata import QWEN_VS_DEEPSEEK_EXPERIMENT_KEY


class _FakePolymarketClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def fetch_market(self, market):
        return {"id": "123", "question": market.question}, "https://fake/markets/123", 200

    def fetch_order_books(self, market):
        return {
            "yes_best_bid": 0.44,
            "yes_best_ask": 0.46,
            "no_best_bid": 0.54,
            "no_best_ask": 0.56,
            "yes_midpoint": 0.45,
            "no_midpoint": 0.55,
            "last_trade_price": 0.45,
            "spread": 0.02,
            "depth_1c": 100.0,
            "depth_3c": 300.0,
            "depth_5c": 500.0,
            "upstream_timestamp": None,
        }


@contextmanager
def _fake_get_session(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _session_factory(engine):
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_market(session) -> Market:
    market = Market(
        platform_market_id="1",
        tracking_id="T-1",
        question="Will the test resolve YES?",
        rules="Resolves YES if the test passes.",
        enabled=True,
        active=True,
        closed=False,
    )
    session.add(market)
    session.flush()
    return market


def _qwen_body(fair_value=0.5) -> dict:
    return {
        "fair_value": fair_value,
        "confidence": 0.5,
        "should_abstain": False,
        "rationale_short": "qwen rationale",
        "key_uncertainties": ["u"],
        "base_rate_notes": "n",
    }


def _deepseek_response(fair_value=0.6, status_code=200) -> object:
    body = {
        "fair_value": fair_value,
        "confidence": 0.6,
        "should_abstain": False,
        "rationale_short": "deepseek rationale",
        "key_uncertainties": ["u"],
        "base_rate_notes": "n",
    }

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = json.dumps({"choices": [{"message": {"content": json.dumps(body)}}]})

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

        def json(self):
            return {
                "model": "deepseek/deepseek-v4-flash-0731",
                "choices": [{"message": {"content": json.dumps(body)}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 90, "total_tokens": 490},
            }

    return _Resp()


def _setup(monkeypatch, tmp_path, *, openrouter_api_key="sk-test", deepseek_status=200, deepseek_fair_value=0.6):
    from sqlalchemy import create_engine

    from app.ppi import pipeline as pipeline_module

    engine = create_engine(f"sqlite:///{tmp_path / 'dual_series.db'}")
    Session = _session_factory(engine)

    settings = Settings(
        llm_provider="openrouter",  # primary series since the 2026-08-26 cutover
        ollama_base_url="http://fake-ollama:11434",
        ollama_model="qwen3:8b",
        openrouter_api_key=openrouter_api_key,
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_model="deepseek/deepseek-v4-flash-0731",
    )
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr(pipeline_module, "init_db", lambda: None)
    monkeypatch.setattr(pipeline_module, "get_session", lambda: _fake_get_session(Session))
    monkeypatch.setattr(pipeline_module, "TrackedPolymarketClient", _FakePolymarketClient)

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            return _deepseek_response(fair_value=deepseek_fair_value, status_code=deepseek_status)

        class _OllamaResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": __import__("json").dumps(_qwen_body())}

        return _OllamaResp()

    monkeypatch.setattr("requests.post", fake_post)
    return pipeline_module, Session


def test_one_evidence_collection_produces_two_isolated_forecast_rows(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path)
    with Session.begin() as session:
        _make_market(session)

    result = pipeline_module.run_daily_pipeline(
        "primary", run_date=date(2026, 8, 14), strict_llm_only=True, lock_path=tmp_path / "p.lock"
    )
    assert result["status"] == "OK"

    with Session() as session:
        rows = list(session.scalars(select(LLMForecast)))
        assert len(rows) == 2
        by_provider = {r.model_provider: r for r in rows}
        assert set(by_provider.keys()) == {"ollama", "openrouter"}
        assert by_provider["ollama"].status == "OK"
        assert by_provider["ollama"].fair_value == 0.5
        assert by_provider["openrouter"].status == "OK"
        assert by_provider["openrouter"].fair_value == 0.6
        # Neither overwrote the other -- both are distinct rows for the same market/run_slot.
        assert by_provider["ollama"].id != by_provider["openrouter"].id
        assert by_provider["ollama"].run_slot == by_provider["openrouter"].run_slot
        assert by_provider["ollama"].market_id == by_provider["openrouter"].market_id
        # Same evidence -> identical prompt_hash (build_prompt is a pure function of the packet,
        # and no evidence-discovery step ran between the two generate_blind_forecast calls).
        assert by_provider["ollama"].prompt_hash == by_provider["openrouter"].prompt_hash
        # Both joined to the same market price only after both forecasts had already terminated.
        assert by_provider["ollama"].comparison_price_at_join == 0.45
        assert by_provider["openrouter"].comparison_price_at_join == 0.45
        assert by_provider["ollama"].raw_ppi == pytest.approx(0.45 - 0.5)
        assert by_provider["openrouter"].raw_ppi == pytest.approx(0.45 - 0.6)


def test_missing_openrouter_key_fails_primary_series_without_touching_qwen_comparison_row(tmp_path, monkeypatch):
    """Missing OPENROUTER_API_KEY -> the primary DeepSeek series fails explicitly
    (MissingAPIKey), zero network calls for that arm -- the Qwen comparison row must be entirely
    unaffected: still OK, still joined. (Pre-2026-08-26 this scenario tested the reverse
    direction, since DeepSeek was the comparison arm; the isolation guarantee itself -- one arm's
    failure never touches the other's row -- is unchanged by the cutover.)"""
    pipeline_module, Session = _setup(monkeypatch, tmp_path, openrouter_api_key="")
    with Session.begin() as session:
        _make_market(session)

    result = pipeline_module.run_daily_pipeline(
        "primary", run_date=date(2026, 8, 14), strict_llm_only=True, lock_path=tmp_path / "p.lock"
    )
    # PARTIAL, not OK: DeepSeek is the primary series post-cutover, so its failure now correctly
    # increments job.error_count (it would not have, pre-cutover, as the comparison arm).
    assert result["status"] == "PARTIAL"

    with Session() as session:
        rows = list(session.scalars(select(LLMForecast)))
        by_provider = {r.model_provider: r for r in rows}
        assert by_provider["ollama"].status == "OK"
        assert by_provider["ollama"].fair_value == 0.5
        assert by_provider["ollama"].comparison_price_at_join == 0.45  # still joined normally
        assert by_provider["openrouter"].status == "FAILED"
        assert by_provider["openrouter"].fair_value is None
        assert "MissingAPIKey" in by_provider["openrouter"].error_message
        # No cross-model fallback: DeepSeek's row is never populated with Qwen's value.
        assert by_provider["openrouter"].model_name == "deepseek/deepseek-v4-flash-0731"


def test_deepseek_malformed_response_records_failure_never_a_salvaged_value(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path, deepseek_status=500)
    with Session.begin() as session:
        _make_market(session)

    result = pipeline_module.run_daily_pipeline(
        "primary", run_date=date(2026, 8, 14), strict_llm_only=True, lock_path=tmp_path / "p.lock"
    )
    # PARTIAL, not OK: DeepSeek is the primary series post-cutover, so its HTTP 500 now correctly
    # increments job.error_count (it would not have, pre-cutover, as the comparison arm).
    assert result["status"] == "PARTIAL"

    with Session() as session:
        rows = list(session.scalars(select(LLMForecast)))
        by_provider = {r.model_provider: r for r in rows}
        assert by_provider["ollama"].status == "OK"  # Qwen comparison row unaffected by the primary series' HTTP 500
        assert by_provider["openrouter"].status == "FAILED"
        assert by_provider["openrouter"].fair_value is None


def test_first_eligible_observation_is_recorded_exactly_once(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path)
    with Session.begin() as session:
        _make_market(session)

    pipeline_module.run_daily_pipeline(
        "primary", run_date=date(2026, 8, 14), strict_llm_only=True, lock_path=tmp_path / "p1.lock"
    )
    pipeline_module.run_daily_pipeline(
        "backup", run_date=date(2026, 8, 14), strict_llm_only=True, lock_path=tmp_path / "p2.lock"
    )

    with Session() as session:
        marker_rows = list(
            session.scalars(
                select(ExperimentMetadata).where(ExperimentMetadata.experiment_key == QWEN_VS_DEEPSEEK_EXPERIMENT_KEY)
            )
        )
        assert len(marker_rows) == 1  # never a second row, even though both runs produced matched pairs
        first_job = session.scalar(select(JobRun).where(JobRun.trigger_type == "primary"))
        assert marker_rows[0].first_job_run_id == first_job.id  # the first run, not the second
