from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.ppi.blind_forecast import FORBIDDEN_PACKET_KEYS
from scripts.run_shadow_experiment import (
    ARMS,
    DecomposedProbabilityEstimate,
    _arm_configs,
    build_decomposed_prompt,
    generate_one,
    reconstruct_frozen_packet,
    run_experiment,
)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _frozen_row(**overrides) -> dict:
    defaults = dict(
        market_id=1,
        market_slug="test-market",
        market_question="Will it happen?",
        market_resolution_criteria="Resolves YES if it happens.",
        market_category="politics",
        market_region="US",
        market_end_date="2026-11-03T00:00:00+00:00",
        evidence_items=[
            {
                "title": "Some article",
                "summary": "A summary of the article.",
                "source_name": "Example News",
                "published_at": "2026-08-01T00:00:00+00:00",
                "category": "news",
            }
        ],
    )
    defaults.update(overrides)
    return defaults


def test_reconstruct_frozen_packet_matches_production_shape():
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    assert packet["question"] == "Will it happen?"
    assert packet["resolution_criteria"] == "Resolves YES if it happens."
    assert packet["category"] == "politics"
    assert packet["region"] == "US"
    assert len(packet["evidence"]) == 1
    assert packet["evidence"][0]["title"] == "Some article"
    assert packet["evidence"][0]["summary"] == "A summary of the article."
    # Never any market-price-derived field -- the same blindness check production uses.
    from app.ppi.blind_forecast import assert_blind_packet

    assert_blind_packet(packet)  # must not raise
    serialized = json.dumps(packet)
    for forbidden in FORBIDDEN_PACKET_KEYS:
        assert forbidden not in serialized


def test_decomposed_prompt_does_not_instruct_avoiding_round_numbers_or_mention_price():
    packet = reconstruct_frozen_packet(_frozen_row())
    prompt = build_decomposed_prompt(packet)
    assert "round number" not in prompt.lower()
    assert "avoid round" not in prompt.lower()
    assert "polymarket" not in prompt.lower()
    assert "market price" not in prompt.lower() or "do not guess the market price" in prompt.lower()
    for field_name in ("base_rate", "market_specific_evidence", "evidence_against", "uncertainty", "fair_value"):
        assert field_name in prompt


def test_arm_configs_match_qwen3_official_recommended_settings():
    configs = _arm_configs()
    assert set(configs.keys()) == set(ARMS)

    # Arm A: exact production replication.
    assert configs["A"].format_json is True
    assert configs["A"].think is None
    assert configs["A"].options["temperature"] == 0.15
    assert configs["A"].options["num_ctx"] == 4096

    # Arm B: Qwen3's official thinking-mode sampling; format=json deliberately off (verified
    # empirically that format=json + think=true together suppress thinking on this Ollama build).
    assert configs["B"].think is True
    assert configs["B"].format_json is False
    assert configs["B"].options["temperature"] == 0.6
    assert configs["B"].options["top_p"] == 0.95
    assert configs["B"].options["top_k"] == 20
    assert configs["B"].options["min_p"] == 0

    # Arm C: Qwen3's official non-thinking sampling.
    assert configs["C"].think is False
    assert configs["C"].options["temperature"] == 0.7
    assert configs["C"].options["top_p"] == 0.8
    assert configs["C"].options["top_k"] == 20

    # Arm D: same settings as Arm A -- only the prompt/schema is the studied variable.
    assert configs["D"].options == configs["A"].options
    assert configs["D"].think is None
    assert configs["D"].schema is DecomposedProbabilityEstimate


def test_generate_one_arm_a_parses_successfully(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert json["format"] == "json"
        assert "think" not in json
        body = {
            "fair_value": 0.45,
            "confidence": 0.6,
            "should_abstain": False,
            "rationale_short": "r",
            "key_uncertainties": ["u"],
            "base_rate_notes": "n",
        }
        return _FakeResponse({"response": __import__("json").dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["A"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "OK"
    assert result.fair_value == 0.45
    assert result.attempts == 1
    assert result.arm == "A"


def test_generate_one_arm_b_parses_from_separate_thinking_field(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert "format" not in json
        assert json["think"] is True
        body = {"fair_value": 0.62, "confidence": 0.7, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse(
            {"response": __import__("json").dumps(body), "thinking": "Let me reason about this step by step..."}
        )

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["B"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "OK"
    assert result.fair_value == 0.62
    assert result.thinking == "Let me reason about this step by step..."


def test_generate_one_arm_d_requires_decomposition_fields(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        body = {
            "base_rate": "Historically about 50% for open seats.",
            "market_specific_evidence": "Recent polling shows a narrow lead.",
            "evidence_against": "Polling has been volatile and unreliable this cycle.",
            "uncertainty": "Turnout is the biggest unknown.",
            "fair_value": 0.53,
            "confidence": 0.55,
        }
        return _FakeResponse({"response": __import__("json").dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["D"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "OK"
    assert result.fair_value == 0.53
    assert result.parsed_fields["base_rate"] == "Historically about 50% for open seats."
    assert result.parsed_fields["evidence_against"] == "Polling has been volatile and unreliable this cycle."


def test_generate_one_arm_d_rejects_missing_decomposition_and_retries_then_fails(monkeypatch):
    call_count = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        call_count["n"] += 1
        # Missing all the required decomposition fields -- always invalid for Arm D's schema.
        body = {"fair_value": 0.5, "confidence": 0.5}
        return _FakeResponse({"response": __import__("json").dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["D"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "FAILED"
    assert result.fair_value is None
    assert call_count["n"] == 3  # initial attempt + MAX_RETRIES(2) retries
    assert "ValidationError" in (result.error_message or "")


def test_generate_one_marks_abstained_forecast_status(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        body = {
            "fair_value": 0.5,
            "confidence": 0.1,
            "should_abstain": True,
            "rationale_short": "Too little evidence.",
        }
        return _FakeResponse({"response": __import__("json").dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["A"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "ABSTAINED"


def test_generate_one_records_http_failure_without_raising(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("requests.post", fake_post)
    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["A"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="http://fake", model="qwen3:8b", timeout=30
    )
    assert result.status == "FAILED"
    assert "ConnectionError" in (result.error_message or "")


def test_arm_f_is_openrouter_deepseek_pinned_and_never_falls_back(monkeypatch):
    configs = _arm_configs()
    assert "F" in ARMS
    assert configs["F"].provider == "openrouter"
    assert configs["F"].schema.__name__ == "BlindFairValueEstimate" or configs["F"].schema is not None

    settings = Settings(
        openrouter_api_key="sk-test",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_model="deepseek/deepseek-v4-flash-0731",
    )
    monkeypatch.setattr("scripts.run_shadow_experiment.get_settings", lambda: settings)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["model"] = json["model"]
        captured["reasoning"] = json.get("reasoning")
        body = {"fair_value": 0.58, "confidence": 0.6, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"choices": [{"message": {"content": __import__("json").dumps(body)}}], "usage": {}})

    monkeypatch.setattr("requests.post", fake_post)

    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, configs["F"], base_url="unused", model="unused", timeout=30
    )

    assert result.status == "OK"
    assert result.fair_value == 0.58
    assert result.arm == "F"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["model"] == "deepseek/deepseek-v4-flash-0731"
    assert captured["reasoning"] == {"enabled": False}


def test_arm_f_failed_request_records_failed_status_no_qwen_fallback(monkeypatch):
    import requests

    settings = Settings(openrouter_api_key="sk-test")
    monkeypatch.setattr("scripts.run_shadow_experiment.get_settings", lambda: settings)

    ollama_called = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        if "chat/completions" in url:
            raise requests.exceptions.ConnectionError("no route to host")
        ollama_called["n"] += 1
        return _FakeResponse({"response": "{}"})

    monkeypatch.setattr("requests.post", fake_post)

    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    config = _arm_configs()["F"]
    result = generate_one(
        packet, row["market_id"], row["market_slug"], 1, config, base_url="unused", model="unused", timeout=30
    )

    assert result.status == "FAILED"
    assert result.fair_value is None
    assert ollama_called["n"] == 0


def test_arm_g_requests_max_reasoning_effort_and_captures_trace(monkeypatch):
    configs = _arm_configs()
    assert "G" in ARMS
    config = configs["G"]
    assert config.provider == "openrouter"
    assert config.openrouter_reasoning == {"enabled": True, "exclude": False, "effort": "max"}
    assert config.openrouter_max_output_tokens == 20000
    assert config.openrouter_timeout == 600

    settings = Settings(openrouter_api_key="sk-test")
    monkeypatch.setattr("scripts.run_shadow_experiment.get_settings", lambda: settings)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        body = {"fair_value": 0.2, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        content = __import__("json").dumps(body)
        message = {
            "role": "assistant",
            "content": content,
            "reasoning": "Starting from a 50% base rate, incumbent-party headwinds push this down...",
            "reasoning_details": [{"type": "text", "text": "step 1..."}],
        }
        return _FakeResponse(
            {
                "model": "deepseek/deepseek-v4-flash-0731",
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 1500, "total_tokens": 2400},
            }
        )

    monkeypatch.setattr("requests.post", fake_post)

    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    result = generate_one(packet, row["market_id"], row["market_slug"], 1, config, base_url="unused", model="unused", timeout=30)

    # Reasoning enabled -> response_format must NOT be forced (mirrors the Ollama format=json +
    # think=true suppression finding; defends against the same risk on this provider).
    assert "response_format" not in captured["json"]
    assert captured["json"]["reasoning"] == {"enabled": True, "exclude": False, "effort": "max"}
    assert captured["json"]["max_tokens"] == 20000

    assert result.status == "OK"
    assert result.fair_value == 0.2
    assert result.reasoning_effort == "max"
    assert "incumbent-party headwinds" in result.reasoning_trace
    assert result.reasoning_details == [{"type": "text", "text": "step 1..."}]
    assert result.market_question == "Will it happen?"
    assert result.evidence_packet == packet
    # cost = 900 * 0.08/1e6 + 1500 * 0.18/1e6
    assert result.estimated_cost_usd == pytest.approx(900 * 0.08e-6 + 1500 * 0.18e-6)


def test_arm_f_reasoning_disabled_still_forces_response_format(monkeypatch):
    """Regression guard: Arm F's (reasoning-disabled) request shape must be unaffected by adding
    Arm G's reasoning-enabled response_format defense."""
    settings = Settings(openrouter_api_key="sk-test")
    monkeypatch.setattr("scripts.run_shadow_experiment.get_settings", lambda: settings)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        body = {"fair_value": 0.5, "confidence": 0.5, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"choices": [{"message": {"content": __import__("json").dumps(body)}}], "usage": {}})

    monkeypatch.setattr("requests.post", fake_post)

    row = _frozen_row()
    packet = reconstruct_frozen_packet(row)
    generate_one(packet, row["market_id"], row["market_slug"], 1, _arm_configs()["F"], base_url="u", model="u", timeout=30)

    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["reasoning"] == {"enabled": False}


def test_run_experiment_writes_incrementally_and_never_touches_a_database(tmp_path, monkeypatch):
    def fake_post(url, json=None, timeout=None):
        body = {"fair_value": 0.45, "confidence": 0.6, "should_abstain": False, "rationale_short": "r"}
        return _FakeResponse({"response": __import__("json").dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)
    rows = [_frozen_row(market_id=1, market_slug="m1"), _frozen_row(market_id=2, market_slug="m2")]
    output_path = tmp_path / "results.json"

    payload = run_experiment(
        rows,
        arms=("A",),
        repetitions=2,
        base_url="http://fake",
        model="qwen3:8b",
        timeout=30,
        output_path=output_path,
    )

    assert len(payload["results"]) == 4  # 1 arm x 2 markets x 2 repetitions
    assert output_path.exists()
    on_disk = json.loads(output_path.read_text())
    assert len(on_disk["results"]) == 4
    assert all(r["arm"] == "A" for r in on_disk["results"])
