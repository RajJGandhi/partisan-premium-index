import json

import pytest
from pydantic import ValidationError

from app.ppi.classifier import (
    DeterministicClassifier,
    EvidenceClassifierProvider,
    OllamaClassifier,
    _normalize_classification_dict,
    classify_with_fallback,
)

VALID_CLASSIFICATION = {
    "relevant": True,
    "relevance_score": 0.9,
    "source_quality": 0.8,
    "changes_probability": True,
    "direction": "YES",
    "estimated_magnitude": 0.1,
    "category": "campaign",
    "summary": "summary",
    "reason": "reason",
    "needs_human_review": True,
}
# Qwen3 occasionally drifts on exact field names under Ollama's default (non-zero) temperature,
# e.g. emitting "relevance" (a bool) instead of the schema's "relevant" -- this reproduces that.
# _normalize_classification_dict repairs this specific drift, so it no longer costs a retry.
MISNAMED_FIELD_RESPONSE = {k: v for k, v in VALID_CLASSIFICATION.items() if k != "relevant"}
MISNAMED_FIELD_RESPONSE["relevance"] = VALID_CLASSIFICATION["relevant"]

# A validation error normalization does *not* cover, to test the genuine retry path still works.
MISSING_CATEGORY_RESPONSE = {k: v for k, v in VALID_CLASSIFICATION.items() if k != "category"}


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": json.dumps(self._payload)}


SAMPLE_PAYLOAD = {
    "market_question": "Will the Republican Party control the Senate after the 2026 Midterm elections?",
    "aliases": [],
    "title": "Example headline",
    "content_text": "Example body",
    "url": "https://example.com/story",
}


def test_normalize_classification_dict_maps_relevance_bool_to_relevant():
    fixed = _normalize_classification_dict({"relevance": True, "relevance_score": 0.9})
    assert fixed["relevant"] is True
    assert "relevance" not in fixed
    assert fixed["relevance_score"] == 0.9


def test_normalize_classification_dict_leaves_correct_input_untouched():
    data = {"relevant": True, "relevance_score": 0.9}
    assert _normalize_classification_dict(data) == data


def test_normalize_classification_dict_does_not_touch_non_bool_relevance():
    # relevance_score (a float) must never be mistaken for the "relevance" typo -- only an actual
    # boolean under "relevance" should be treated as the drifted "relevant" field.
    data = {"relevance": 0.9}
    fixed = _normalize_classification_dict(data)
    assert "relevant" not in fixed
    assert fixed == data


def test_ollama_classifier_normalizes_relevance_field_drift_without_a_retry(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(MISNAMED_FIELD_RESPONSE)

    monkeypatch.setattr("requests.post", fake_post)
    classifier = OllamaClassifier("http://fake-ollama:11434", "qwen3:8b", 30)
    result = classifier.classify(SAMPLE_PAYLOAD)

    assert result.relevant is True
    assert len(calls) == 1  # the known "relevance"->"relevant" drift is repaired inline, no retry needed
    assert calls[0]["options"]["temperature"] == 0.15


def test_ollama_classifier_retries_once_on_other_validation_errors_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        payload = MISSING_CATEGORY_RESPONSE if len(calls) == 1 else VALID_CLASSIFICATION
        return _FakeResponse(payload)

    monkeypatch.setattr("requests.post", fake_post)
    classifier = OllamaClassifier("http://fake-ollama:11434", "qwen3:8b", 30)
    result = classifier.classify(SAMPLE_PAYLOAD)

    assert result.relevant is True
    assert len(calls) == 2
    # The corrective retry must tell the model what it got wrong.
    assert "previous response was invalid" in calls[1]["prompt"]


def test_ollama_classifier_raises_after_two_failed_attempts_and_fallback_catches_it(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return _FakeResponse(MISSING_CATEGORY_RESPONSE)

    monkeypatch.setattr("requests.post", fake_post)
    classifier = OllamaClassifier("http://fake-ollama:11434", "qwen3:8b", 30)

    with pytest.raises(ValidationError):
        classifier.classify(SAMPLE_PAYLOAD)

    result, warning = classify_with_fallback(SAMPLE_PAYLOAD, classifier)
    assert warning is not None and "fallback" in warning
    assert result.relevant is not None


class BrokenProvider(EvidenceClassifierProvider):
    name = "broken"
    model = "broken"

    def classify(self, payload):
        raise ValueError("malformed model response")


def test_classifier_schema_and_deterministic_relevance():
    result = DeterministicClassifier().classify(
        {
            "market_question": "Will the Democrats win the Maine Senate race in 2026?",
            "aliases": ["Maine Senate"],
            "title": "New Maine Senate poll shows a close race",
            "content_text": "A statewide survey of likely voters",
            "url": "https://reuters.com/example",
        }
    )
    assert result.relevant is True
    assert result.category == "polling"
    assert 0 <= result.relevance_score <= 1


def test_malformed_provider_falls_back_without_breaking_job():
    result, warning = classify_with_fallback(
        {
            "market_question": "Will the Republicans win the Texas Senate race in 2026?",
            "aliases": ["Texas Senate"],
            "title": "Texas Senate ballot ruling issued",
            "content_text": "",
            "url": "https://example.com/story",
        },
        BrokenProvider(),
    )
    assert result.relevant is True
    assert "fallback" in warning
