from __future__ import annotations

import json
import json as json_lib  # alias used inside fake_post closures whose `json=None` param shadows the module
from datetime import date, datetime, timezone

import pytest
import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.database import Base
from app.db.models import JobRun, LLMForecast, Market
from app.ppi.blind_forecast import (
    OPENROUTER_APP_TITLE,
    OPENROUTER_REFERER,
    PRIMARY_SERIES_PROVIDERS,
    default_provider_config,
    generate_blind_forecast,
    is_matched_pair,
    openrouter_provider_config,
)
from app.ppi.run_classification import compute_run_classification


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'openrouter.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_market(session) -> Market:
    market = Market(
        platform_market_id="1",
        tracking_id="T-1",
        question="Will the test resolve YES?",
        rules="Resolves YES if the test passes.",
        enabled=True,
    )
    session.add(market)
    session.flush()
    return market


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._payload


def _deepseek_body(**overrides) -> dict:
    body = {
        "fair_value": 0.58,
        "confidence": 0.6,
        "should_abstain": False,
        "rationale_short": "r",
        "key_uncertainties": ["u"],
        "base_rate_notes": "n",
    }
    body.update(overrides)
    return {
        "id": "gen-1",
        "model": "deepseek/deepseek-v4-flash-0731",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(body)}}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 80, "total_tokens": 580},
    }


def _openrouter_settings(**overrides) -> Settings:
    defaults = dict(
        llm_provider="ollama",  # primary series config is untouched by any of this
        openrouter_api_key="sk-test-key",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_model="deepseek/deepseek-v4-flash-0731",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# --- Request shape ----------------------------------------------------------------------------


def test_openrouter_request_shape_model_headers_reasoning_response_format(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(_deepseek_body())

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="test-run",
            trigger_type="manual",
            day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc),
            provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "OK"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    assert captured["headers"]["HTTP-Referer"] == OPENROUTER_REFERER
    assert captured["headers"]["X-OpenRouter-Title"] == OPENROUTER_APP_TITLE
    assert captured["json"]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["reasoning"] == {"enabled": False}  # fixed, recorded first-test config
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["role"] == "user"


def test_pinned_model_is_sent_verbatim_never_an_alias_or_auto_route(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(openrouter_model="deepseek/deepseek-v4-flash-0731")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    captured = {}
    monkeypatch.setattr(
        "requests.post",
        lambda url, headers=None, json=None, timeout=None: (captured.update(json=json), _FakeResponse(_deepseek_body()))[1],
    )

    with Session.begin() as session:
        market = _make_market(session)
        generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert captured["json"]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert "auto" not in captured["json"]["model"]
    assert "latest" not in captured["json"]["model"]


# --- Missing API key ---------------------------------------------------------------------------


def test_missing_api_key_fails_explicitly_without_any_network_call(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(openrouter_api_key="")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    called = {"n": 0}
    monkeypatch.setattr("requests.post", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert forecast.fair_value is None
    assert "MissingAPIKey" in forecast.error_message
    assert called["n"] == 0  # never even attempted the network call


# --- No secret logging ---------------------------------------------------------------------------


def test_api_key_never_appears_in_persisted_error_or_raw_response(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(openrouter_api_key="sk-super-secret-value")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr(
        "requests.post", lambda url, headers=None, json=None, timeout=None: _FakeResponse({}, status_code=500)
    )

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert "sk-super-secret-value" not in (forecast.error_message or "")
    assert "sk-super-secret-value" not in (forecast.raw_response or "")
    assert "sk-super-secret-value" not in (forecast.generation_params_json or "")


# --- Malformed response --------------------------------------------------------------------------


def test_malformed_response_records_failed_status_not_a_crash(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr(
        "requests.post",
        lambda url, headers=None, json=None, timeout=None: _FakeResponse({"choices": []}),  # no message/content
    )

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert "MalformedResponse" in forecast.error_message


# --- Timeout ---------------------------------------------------------------------------------------


def test_timeout_records_failed_status_with_explicit_error(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    def raise_timeout(url, headers=None, json=None, timeout=None):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr("requests.post", raise_timeout)

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert "Timeout" in forecast.error_message


# --- Rate limiting -----------------------------------------------------------------------------


def test_rate_limit_429_records_failed_status_distinctly(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr(
        "requests.post",
        lambda url, headers=None, json=None, timeout=None: _FakeResponse(
            {"error": "rate limited"}, status_code=429, text="rate limit exceeded"
        ),
    )

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert "RateLimited" in forecast.error_message
    assert "429" in forecast.error_message


# --- Failed request, no fallback ----------------------------------------------------------------


def test_failed_deepseek_request_never_falls_back_to_qwen_or_any_other_model(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    ollama_called = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            raise requests.exceptions.ConnectionError("no route to host")
        ollama_called["n"] += 1  # would only happen if something wrongly fell back to Ollama's shape
        return _FakeResponse({"response": "{}"})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.status == "FAILED"
    assert forecast.fair_value is None
    assert forecast.model_provider == "openrouter"  # never silently relabeled as ollama/qwen either
    assert ollama_called["n"] == 0


# --- Blindness enforcement on the OpenRouter path specifically -----------------------------------


def test_openrouter_prompt_never_contains_forbidden_market_fields(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    captured_bodies = []

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_bodies.append(json)
        return _FakeResponse(_deepseek_body())

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        from app.db.models import MarketSnapshot

        market = _make_market(session)
        session.add(
            MarketSnapshot(market_id=market.id, snapshot_date=date(2026, 8, 12), snapshot_kind="daily", comparison_price=0.77)
        )
        session.flush()
        generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert captured_bodies
    for body in captured_bodies:
        serialized = json.dumps(body)
        assert "0.77" not in serialized
        for forbidden in ("comparison_price", "yes_best_bid", "best_ask", "midpoint"):
            assert forbidden not in serialized


# --- Provider/model provenance, raw-response persistence, token/cost metadata --------------------


def test_provenance_raw_response_and_token_usage_all_persisted(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings()
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr(
        "requests.post", lambda url, headers=None, json=None, timeout=None: _FakeResponse(_deepseek_body(fair_value=0.61))
    )

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert forecast.model_provider == "openrouter"
    assert forecast.model_name == "deepseek/deepseek-v4-flash-0731"
    assert forecast.fair_value == pytest.approx(0.61)
    assert forecast.raw_response and "0.61" in forecast.raw_response
    params = json.loads(forecast.generation_params_json)
    assert params["usage"]["prompt_tokens"] == 500
    assert params["usage"]["completion_tokens"] == 80
    assert params["usage"]["total_tokens"] == 580
    assert params["usage"]["served_model"] == "deepseek/deepseek-v4-flash-0731"
    assert params["reasoning"] == {"enabled": False}
    # Cost metadata must never influence the scored fields -- nothing in fair_value/confidence/
    # status is derived from usage; this is purely descriptive.
    assert set(["fair_value", "confidence", "status"]).isdisjoint(params["usage"].keys())


# --- Same-evidence parity across matched model forecasts ------------------------------------------


def test_same_evidence_packet_used_for_both_ollama_and_openrouter_calls(tmp_path, monkeypatch):
    """A matched V1-vs-DeepSeek comparison requires byte-identical evidence input to both calls."""
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    captured_prompts = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            captured_prompts["openrouter"] = json["messages"][1]["content"]
            return _FakeResponse(_deepseek_body())
        captured_prompts["ollama"] = json["prompt"]
        body = {"fair_value": 0.5, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"response": json_module.dumps(body)})

    import json as json_module

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=default_provider_config(settings),
        )
        generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    # Ollama's payload embeds SYSTEM_INSTRUCTIONS + prompt together; OpenRouter's is the user-turn
    # prompt alone (system message separate) -- so compare the shared evidence-bearing suffix.
    assert captured_prompts["openrouter"] in captured_prompts["ollama"]


# --- Separation of diagnostic and canonical series -------------------------------------------------


def test_second_provider_never_overwrites_or_skips_first_providers_row(tmp_path, monkeypatch):
    """Regression test for the exact bug found during planning: before the (market_id, run_slot,
    model_provider) uniqueness widening, a second provider's forecast for the same market/slot
    would either no-op (if the first was OK) or silently overwrite the first provider's row."""
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            return _FakeResponse(_deepseek_body(fair_value=0.71))
        body = {"fair_value": 0.33, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"response": json_lib.dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        qwen_forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=default_provider_config(settings),
        )
        deepseek_forecast = generate_blind_forecast(
            session, market, job=None, run_key="r", trigger_type="manual", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

        rows = list(session.scalars(select(LLMForecast).where(LLMForecast.market_id == market.id)))

    assert len(rows) == 2  # independent rows, neither overwritten
    assert qwen_forecast.id != deepseek_forecast.id
    assert qwen_forecast.fair_value == pytest.approx(0.33)
    assert qwen_forecast.model_provider == "ollama"
    assert deepseek_forecast.fair_value == pytest.approx(0.71)
    assert deepseek_forecast.model_provider == "openrouter"
    # Same run_slot, distinguished only by provider.
    assert qwen_forecast.run_slot == deepseek_forecast.run_slot == "2026-08-12:primary"


def test_openrouter_series_never_flips_primary_series_canonical_classification(tmp_path, monkeypatch):
    """A contaminated/failed DeepSeek forecast sharing a run_key must never degrade Qwen's own
    canonical classification -- compute_run_classification is scoped to PRIMARY_SERIES_PROVIDERS."""
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            return _FakeResponse(_deepseek_body())
        body = {"fair_value": 0.5, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"response": json_lib.dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        job = JobRun(run_key="r", job_name="daily_pipeline", trigger_type="primary", status="OK", pipeline_mode="strict_llm_only")
        session.add(job)
        session.flush()
        market = _make_market(session)

        qwen_forecast = generate_blind_forecast(
            session, market, job=job, run_key="r", trigger_type="primary", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=default_provider_config(settings),
            strict=True,
        )
        qwen_forecast.evidence_all_live_classified = True  # simulate a clean strict Qwen forecast

        deepseek_forecast = generate_blind_forecast(
            session, market, job=job, run_key="r", trigger_type="primary", day=date(2026, 8, 12),
            now=datetime(2026, 8, 12, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )
        # DeepSeek's evidence_all_live_classified is left False/None here to simulate contamination
        # that must NOT affect Qwen's own classification.
        deepseek_forecast.evidence_all_live_classified = False
        session.flush()

        classification = compute_run_classification(session, job, "r")

    assert "openrouter" not in PRIMARY_SERIES_PROVIDERS
    assert classification == "canonical"  # unaffected by the DeepSeek row's contamination


# --- is_matched_pair: the evidence-hash matched-observation invariant -----------------------------


def test_is_matched_pair_true_for_two_ok_forecasts_with_identical_prompt_hash():
    a = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="OK", prompt_hash="abc",
        model_provider="ollama", model_name="qwen3:8b", prompt_version="fair_value_v0.1",
    )
    b = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="OK", prompt_hash="abc",
        model_provider="openrouter", model_name="deepseek/deepseek-v4-flash-0731", prompt_version="fair_value_v0.1",
    )
    assert is_matched_pair(a, b) is True


def test_is_matched_pair_false_when_prompt_hashes_differ():
    """The core evidence-integrity check: two rows for the same market/slot/job are NOT a valid
    matched pair unless their evidence-derived prompt_hash values are identical."""
    a = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="OK", prompt_hash="abc",
        model_provider="ollama", model_name="qwen3:8b", prompt_version="fair_value_v0.1",
    )
    b = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="OK", prompt_hash="different",
        model_provider="openrouter", model_name="deepseek/deepseek-v4-flash-0731", prompt_version="fair_value_v0.1",
    )
    assert is_matched_pair(a, b) is False


def test_is_matched_pair_false_when_either_side_is_not_ok():
    ok = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="OK", prompt_hash="abc",
        model_provider="ollama", model_name="qwen3:8b", prompt_version="fair_value_v0.1",
    )
    failed = LLMForecast(
        market_id=1, run_slot="s", job_run_id=7, status="FAILED", prompt_hash="abc",
        model_provider="openrouter", model_name="deepseek/deepseek-v4-flash-0731", prompt_version="fair_value_v0.1",
    )
    assert is_matched_pair(ok, failed) is False
    assert is_matched_pair(failed, ok) is False


def test_is_matched_pair_false_for_different_market_or_run_slot_or_job_run():
    base = dict(prompt_hash="abc", status="OK", model_provider="ollama", model_name="qwen3:8b", prompt_version="fair_value_v0.1")
    a = LLMForecast(market_id=1, run_slot="s", job_run_id=7, **base)
    different_market = LLMForecast(market_id=2, run_slot="s", job_run_id=7, **base)
    different_slot = LLMForecast(market_id=1, run_slot="other", job_run_id=7, **base)
    different_job = LLMForecast(market_id=1, run_slot="s", job_run_id=8, **base)
    assert is_matched_pair(a, different_market) is False
    assert is_matched_pair(a, different_slot) is False
    assert is_matched_pair(a, different_job) is False


def test_real_dual_generation_produces_a_matched_pair_with_equal_prompt_hash(tmp_path, monkeypatch):
    """End-to-end: generating Qwen then DeepSeek from the exact same, already-persisted evidence
    (no evidence-discovery step in between) must yield two rows whose prompt_hash values are
    identical, and is_matched_pair must recognize them as matched."""
    Session = _session_factory(tmp_path)
    settings = _openrouter_settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            return _FakeResponse(_deepseek_body(fair_value=0.61))
        body = {"fair_value": 0.5, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"response": json_lib.dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        job = JobRun(run_key="r", job_name="daily_pipeline", trigger_type="primary", status="OK")
        session.add(job)
        session.flush()

        qwen_forecast = generate_blind_forecast(
            session, market, job=job, run_key="r", trigger_type="primary", day=date(2026, 8, 14),
            now=datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc), provider_config=default_provider_config(settings),
        )
        deepseek_forecast = generate_blind_forecast(
            session, market, job=job, run_key="r", trigger_type="primary", day=date(2026, 8, 14),
            now=datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc), provider_config=openrouter_provider_config(settings),
        )

    assert qwen_forecast.status == "OK"
    assert deepseek_forecast.status == "OK"
    assert qwen_forecast.prompt_hash == deepseek_forecast.prompt_hash
    assert is_matched_pair(qwen_forecast, deepseek_forecast) is True
