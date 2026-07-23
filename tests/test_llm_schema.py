import pytest

from app.llm.ollama_client import OllamaJSONClient
from app.llm.schemas import MarketClassifierOutput, ResolutionRiskOutput


def test_market_classifier_output_validates():
    data = {
        "market_category": "election",
        "emotional_side": "YES",
        "ideological_coding": "right_populist",
        "identity_intensity": 4,
        "institutional_friction": 2,
        "deadline_decay_relevance": 1,
        "classification_confidence": 0.75,
        "summary": "Example",
        "warnings": [],
    }
    assert MarketClassifierOutput.model_validate(data).market_category == "election"


def test_resolution_parser_output_validates():
    data = {
        "resolution_risk": 2,
        "ambiguous_terms": ["announced"],
        "source_dependency": "official",
        "implementation_vs_announcement_risk": 1,
        "date_boundary_risk": 1,
        "summary": "Example",
        "warnings": [],
    }
    assert ResolutionRiskOutput.model_validate(data).resolution_risk == 2


def test_invalid_json_is_retried_once(monkeypatch):
    calls = {"n": 0}

    def fake_generate_json(self, system_prompt, user_payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"bad": "schema"}
        return {
            "market_category": "other",
            "emotional_side": "unclear",
            "ideological_coding": "unclear",
            "identity_intensity": 0,
            "institutional_friction": 0,
            "deadline_decay_relevance": 0,
            "classification_confidence": 0.0,
            "summary": "Recovered",
            "warnings": [],
        }

    monkeypatch.setattr(OllamaJSONClient, "generate_json", fake_generate_json)
    client = OllamaJSONClient()
    result = client.generate_validated("system", {"x": 1}, MarketClassifierOutput)
    assert result.summary == "Recovered"
    assert calls["n"] == 2
