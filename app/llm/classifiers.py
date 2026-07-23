from __future__ import annotations

from typing import Any

from app.llm.ollama_client import OllamaJSONClient
from app.llm.prompts import (
    CROSS_MARKET_MATCHER_SYSTEM,
    MARKET_CLASSIFIER_SYSTEM,
    NEWS_MATERIALITY_SYSTEM,
    PUBLIC_EXPLANATION_SYSTEM,
    RESOLUTION_RISK_SYSTEM,
)
from app.llm.schemas import (
    CrossMarketMatcherOutput,
    MarketClassifierOutput,
    NewsCampaignMaterialityOutput,
    PublicExplanationOutput,
    ResolutionRiskOutput,
)


def unavailable_classification(reason: str) -> MarketClassifierOutput:
    return MarketClassifierOutput(
        market_category="other",
        emotional_side="unclear",
        ideological_coding="unclear",
        identity_intensity=0,
        institutional_friction=0,
        deadline_decay_relevance=0,
        classification_confidence=0.0,
        summary="Classification unavailable.",
        warnings=[reason],
    )


def unavailable_resolution(reason: str) -> ResolutionRiskOutput:
    return ResolutionRiskOutput(
        resolution_risk=3,
        ambiguous_terms=[],
        source_dependency="unclear",
        implementation_vs_announcement_risk=0,
        date_boundary_risk=0,
        summary="Resolution parsing unavailable; using conservative default.",
        warnings=[reason],
    )


class LocalLLMClassifiers:
    def __init__(self, client: OllamaJSONClient | None = None):
        self.client = client or OllamaJSONClient()

    def classify_market(self, payload: dict[str, Any]) -> MarketClassifierOutput:
        try:
            return self.client.generate_validated(MARKET_CLASSIFIER_SYSTEM, payload, MarketClassifierOutput)
        except Exception as exc:
            return unavailable_classification(str(exc))

    def parse_resolution_risk(self, payload: dict[str, Any]) -> ResolutionRiskOutput:
        try:
            return self.client.generate_validated(RESOLUTION_RISK_SYSTEM, payload, ResolutionRiskOutput)
        except Exception as exc:
            return unavailable_resolution(str(exc))

    def match_cross_market(self, payload: dict[str, Any]) -> CrossMarketMatcherOutput:
        return self.client.generate_validated(CROSS_MARKET_MATCHER_SYSTEM, payload, CrossMarketMatcherOutput)

    def classify_news_materiality(self, payload: dict[str, Any]) -> NewsCampaignMaterialityOutput:
        return self.client.generate_validated(NEWS_MATERIALITY_SYSTEM, payload, NewsCampaignMaterialityOutput)

    def write_public_explanation(self, payload: dict[str, Any]) -> PublicExplanationOutput:
        return self.client.generate_validated(PUBLIC_EXPLANATION_SYSTEM, payload, PublicExplanationOutput)
