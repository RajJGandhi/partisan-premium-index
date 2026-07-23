from app.ppi.classifier import DeterministicClassifier, EvidenceClassifierProvider, classify_with_fallback


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
