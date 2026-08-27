from __future__ import annotations

from datetime import date

import pytest

from app.blind.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTIONS,
    assert_prompt_market_free,
    build_blind_prompt,
    prompt_hash,
)
from app.quant.engine import run_quant_forecast
from app.quant.evidence_bundle import build_quant_evidence_bundle


def _bundle(make_input, make_poll):
    inp = make_input(
        polls=[make_poll(pollster="A"), make_poll(pollster="B", end_date=date(2026, 8, 15))],
        dem_incumbent=True,
    )
    return inp, build_quant_evidence_bundle(inp, run_quant_forecast(inp))


CONTRACT = "Will the Democratic candidate win the TX senate general election in 2026?"


def test_prompt_contains_evidence_and_no_market_data(make_input, make_poll):
    _inp, bundle = _bundle(make_input, make_poll)
    text = build_blind_prompt(bundle, contract_question=CONTRACT)
    assert CONTRACT in text
    assert "POLLS" in text and "FUNDAMENTALS CONTEXT" in text
    assert "state_lean" in text
    # the only permitted mention of "market price" is the negative reminder
    assert "not given the market price" in text.lower()


def test_prompt_version_stable():
    assert PROMPT_VERSION == "blind_benchmark_v1"


def test_prompt_hash_deterministic(make_input, make_poll):
    _inp, bundle = _bundle(make_input, make_poll)
    u = build_blind_prompt(bundle, contract_question=CONTRACT)
    assert prompt_hash(SYSTEM_INSTRUCTIONS, u) == prompt_hash(SYSTEM_INSTRUCTIONS, u)
    assert len(prompt_hash(SYSTEM_INSTRUCTIONS, u)) == 64


def test_assert_prompt_market_free_rejects_leaks():
    with pytest.raises(ValueError):
        assert_prompt_market_free("The Polymarket price is 0.62 for this race.")
    with pytest.raises(ValueError):
        assert_prompt_market_free("betting odds have shifted toward the incumbent")
    with pytest.raises(ValueError):
        assert_prompt_market_free("yes_best_bid: 0.55\nyes_best_ask: 0.57")
    # the negative reminder sentence is allowed
    assert_prompt_market_free(
        "REMINDER: You are not given the market price, bid, ask, spread, volume for any contract."
    )


def test_build_blind_prompt_raises_if_bundle_carries_market_field(make_input, make_poll):
    _inp, bundle = _bundle(make_input, make_poll)
    # inject a forbidden key into a copy of the payload and rebuild
    poisoned = dict(bundle.payload)
    poisoned["polling_average"] = {**(poisoned.get("polling_average") or {}), "market_probability": 0.7}
    from dataclasses import replace

    bad = replace(bundle, payload=poisoned)
    with pytest.raises(ValueError):
        build_blind_prompt(bad, contract_question=CONTRACT)


def test_system_instructions_prohibit_market_data():
    low = SYSTEM_INSTRUCTIONS.lower()
    assert "no access to prediction-market" in low
    assert "polymarket" in low and "kalshi" in low
