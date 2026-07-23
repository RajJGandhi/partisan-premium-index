from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Score0To5 = int


def clamp_int_0_5(value: int) -> int:
    return max(0, min(5, int(value)))


class MarketClassifierOutput(BaseModel):
    market_category: Literal[
        "election",
        "policy_deadline",
        "legal_process",
        "culture_war",
        "appointment",
        "resignation",
        "geopolitics",
        "crypto_policy",
        "other",
    ]
    emotional_side: Literal["YES", "NO", "unclear"]
    ideological_coding: Literal[
        "right_populist",
        "right_establishment",
        "left_populist",
        "left_establishment",
        "centrist_establishment",
        "crypto_bullish",
        "anti_institutional",
        "unclear",
        "none",
    ]
    identity_intensity: int = Field(ge=0, le=5)
    institutional_friction: int = Field(ge=0, le=5)
    deadline_decay_relevance: int = Field(ge=0, le=5)
    classification_confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class ResolutionRiskOutput(BaseModel):
    resolution_risk: int = Field(ge=0, le=5)
    ambiguous_terms: list[str] = Field(default_factory=list)
    source_dependency: Literal["official", "media_call", "oracle_discretion", "unclear", "none"]
    implementation_vs_announcement_risk: int = Field(ge=0, le=5)
    date_boundary_risk: int = Field(ge=0, le=5)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class CrossMarketMatcherOutput(BaseModel):
    same_underlying_event: bool
    matching_confidence: float = Field(ge=0.0, le=1.0)
    material_differences: list[str] = Field(default_factory=list)
    safe_to_compare_prices: bool
    summary: str = ""


class NewsCampaignMaterialityOutput(BaseModel):
    materiality: Literal["none", "low", "medium", "high"]
    direction: Literal["helps_yes", "helps_no", "mixed", "unclear"]
    probability_adjustment_suggestion: float = Field(ge=-0.05, le=0.05)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class PublicExplanationOutput(BaseModel):
    short_alert: str
    public_explanation: str
    private_notes: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)
