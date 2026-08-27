"""Structured output contract for a blind benchmark forecast (spec sections 23, 24)."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

RESPONSE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "probability": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Your independent probability that the stated binary event resolves YES.",
        },
        "should_abstain": {
            "type": "boolean",
            "description": "true if the supplied evidence is insufficient for a meaningful estimate.",
        },
        "rationale": {"type": "string", "maxLength": 900},
        "uncertainty_drivers": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
            "description": "The main things that could move this estimate.",
        },
        "base_rate_notes": {"type": "string", "maxLength": 600},
    },
    "required": ["probability", "should_abstain", "rationale", "uncertainty_drivers"],
}


class BlindForecastResponse(BaseModel):
    probability: float = Field(ge=0.0, le=1.0)
    should_abstain: bool = False
    rationale: str = Field(min_length=1, max_length=1500)
    uncertainty_drivers: list[str] = Field(default_factory=list, max_length=8)
    base_rate_notes: str = Field(default="", max_length=1200)


class BlindResponseParseError(ValueError):
    """The model output could not be parsed into a valid :class:`BlindForecastResponse`."""


def _extract_json_object(text: str) -> str:
    """Pull the first balanced ``{...}`` object out of a model response.

    Handles ```json fences, ``<think>`` preludes, and trailing prose, matching the tolerance of the
    repo's existing ``app/ppi/blind_forecast.py`` parser.
    """
    if text is None:
        raise BlindResponseParseError("empty response")
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        return fence.group(1)
    start = cleaned.find("{")
    if start == -1:
        raise BlindResponseParseError("no JSON object found in response")
    depth = 0
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : i + 1]
    raise BlindResponseParseError("unbalanced JSON braces in response")


def parse_blind_response(text: str) -> BlindForecastResponse:
    raw = _extract_json_object(text)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BlindResponseParseError(f"invalid JSON: {exc}") from exc
    try:
        return BlindForecastResponse.model_validate(obj)
    except ValidationError as exc:
        raise BlindResponseParseError(f"schema validation failed: {exc}") from exc
