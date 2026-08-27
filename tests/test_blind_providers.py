from __future__ import annotations

import json

from app.blind.providers import (
    AnthropicBlindProvider,
    DeterministicBlindProvider,
    OpenAIBlindProvider,
)
from app.blind.schema import parse_blind_response


def test_real_providers_disabled_without_key():
    o = OpenAIBlindProvider()
    o._api_key = ""
    a = AnthropicBlindProvider()
    a._api_key = ""
    assert o.enabled() is False
    assert a.enabled() is False


def test_enabled_with_key_depends_only_on_sdk_presence():
    o = OpenAIBlindProvider()
    o._api_key = "sk-test"
    # enabled() is True iff the `openai` package is importable; either way it must not raise
    assert isinstance(o.enabled(), bool)
    a = AnthropicBlindProvider()
    a._api_key = "sk-ant-test"
    assert isinstance(a.enabled(), bool)


def test_provider_names_and_models():
    assert OpenAIBlindProvider().provider_name == "openai"
    assert AnthropicBlindProvider().provider_name == "anthropic"
    assert AnthropicBlindProvider(model="claude-opus-5").model_name == "claude-opus-5"


def test_deterministic_stub_is_labelled_and_produces_valid_json(blind_bundle):
    p = DeterministicBlindProvider(bundle=blind_bundle, standing_in_for="openai")
    assert p.is_stub is True
    assert p.model_name == "deterministic-benchmark-stub"
    assert p.provider_name == "openai"
    call = p.generate(system="s", user="u")
    parsed = parse_blind_response(call.raw_text)
    assert 0.0 <= parsed.probability <= 1.0
    assert "[STUB]" in parsed.rationale
    assert call.total_tokens == 0


def test_deterministic_stub_two_slots_differ_but_reproducible(blind_bundle):
    a1 = json.loads(
        DeterministicBlindProvider(bundle=blind_bundle, standing_in_for="openai").generate(system="", user="").raw_text
    )
    a2 = json.loads(
        DeterministicBlindProvider(bundle=blind_bundle, standing_in_for="openai").generate(system="", user="").raw_text
    )
    b = json.loads(
        DeterministicBlindProvider(bundle=blind_bundle, standing_in_for="anthropic").generate(system="", user="").raw_text
    )
    assert a1["probability"] == a2["probability"]  # reproducible
    assert a1["probability"] != b["probability"]  # independent per slot
