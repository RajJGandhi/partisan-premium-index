from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

WEIGHTS = {
    "polling_prob": 0.35,
    "forecast_prob": 0.25,
    "other_markets_prob": 0.20,
    "expert_prob": 0.10,
    "news_campaign_prob": 0.10,
}


@dataclass(frozen=True)
class FairValueResult:
    fair_yes: float | None
    adjusted_confidence: float
    used_manual: bool
    missing_components: list[str]
    normalized_weights: dict[str, float]
    warnings: list[str]


def validate_probability(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    v = float(value)
    if not 0 <= v <= 1:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
    return v


def compute_fair_value(
    polling_prob: float | None = None,
    forecast_prob: float | None = None,
    other_markets_prob: float | None = None,
    expert_prob: float | None = None,
    news_campaign_prob: float | None = None,
    manual_fair_yes: float | None = None,
    confidence: float | None = None,
) -> FairValueResult:
    base_confidence = validate_probability("confidence", confidence) if confidence is not None else 0.0
    manual = validate_probability("manual_fair_yes", manual_fair_yes)
    if manual is not None:
        return FairValueResult(
            fair_yes=manual,
            adjusted_confidence=base_confidence or 0.60,
            used_manual=True,
            missing_components=[],
            normalized_weights={"manual_fair_yes": 1.0},
            warnings=[],
        )

    components = {
        "polling_prob": validate_probability("polling_prob", polling_prob),
        "forecast_prob": validate_probability("forecast_prob", forecast_prob),
        "other_markets_prob": validate_probability("other_markets_prob", other_markets_prob),
        "expert_prob": validate_probability("expert_prob", expert_prob),
        "news_campaign_prob": validate_probability("news_campaign_prob", news_campaign_prob),
    }
    available = {k: v for k, v in components.items() if v is not None}
    missing = [k for k, v in components.items() if v is None]
    if not available:
        return FairValueResult(
            fair_yes=None,
            adjusted_confidence=0.0,
            used_manual=False,
            missing_components=missing,
            normalized_weights={},
            warnings=["No fair-value components available."],
        )
    weight_sum = sum(WEIGHTS[k] for k in available)
    normalized_weights = {k: WEIGHTS[k] / weight_sum for k in available}
    fair_yes = sum(float(available[k]) * normalized_weights[k] for k in available)

    completeness = weight_sum
    # Missing components do not destroy the signal, but they should be visible and penalized.
    adjusted = base_confidence * completeness if base_confidence else max(0.25, completeness * 0.65)
    adjusted = max(0.0, min(1.0, adjusted))
    warnings = []
    if missing:
        warnings.append(f"Missing fair-value components: {', '.join(missing)}")
    return FairValueResult(
        fair_yes=fair_yes,
        adjusted_confidence=adjusted,
        used_manual=False,
        missing_components=missing,
        normalized_weights=normalized_weights,
        warnings=warnings,
    )
