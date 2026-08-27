from __future__ import annotations

import pytest

from app.blind.schema import (
    RESPONSE_JSON_SCHEMA,
    BlindResponseParseError,
    parse_blind_response,
)


def test_parses_clean_json():
    r = parse_blind_response(
        '{"probability": 0.63, "should_abstain": false, "rationale": "lean D state, D incumbent",'
        ' "uncertainty_drivers": ["turnout", "late polls"], "base_rate_notes": "incumbents ~80%"}'
    )
    assert r.probability == 0.63 and r.should_abstain is False
    assert r.uncertainty_drivers == ["turnout", "late polls"]


def test_parses_json_in_code_fence_with_prose():
    r = parse_blind_response(
        'Here is my estimate.\n```json\n{"probability": 0.4, "should_abstain": true, '
        '"rationale": "thin evidence", "uncertainty_drivers": []}\n```\nThanks.'
    )
    assert r.probability == 0.4 and r.should_abstain is True


def test_strips_think_block():
    r = parse_blind_response(
        '<think>let me reason...</think>{"probability": 0.5, "should_abstain": false, '
        '"rationale": "tossup", "uncertainty_drivers": ["everything"]}'
    )
    assert r.probability == 0.5


def test_rejects_out_of_range_probability():
    with pytest.raises(BlindResponseParseError):
        parse_blind_response('{"probability": 1.4, "should_abstain": false, "rationale": "x", "uncertainty_drivers": []}')


def test_rejects_missing_required_field():
    with pytest.raises(BlindResponseParseError):
        parse_blind_response('{"should_abstain": false, "rationale": "x", "uncertainty_drivers": []}')


def test_rejects_non_json():
    with pytest.raises(BlindResponseParseError):
        parse_blind_response("I think it's about 60 percent likely.")
    with pytest.raises(BlindResponseParseError):
        parse_blind_response("")


def test_json_schema_shape():
    assert RESPONSE_JSON_SCHEMA["required"] == ["probability", "should_abstain", "rationale", "uncertainty_drivers"]
    assert RESPONSE_JSON_SCHEMA["properties"]["probability"]["maximum"] == 1.0
