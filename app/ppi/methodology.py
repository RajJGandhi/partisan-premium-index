from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

COMPONENT_TYPES = ("polling", "forecast", "comparable", "expert", "news")
DEFAULT_WEIGHTS = {
    "polling": 0.35,
    "forecast": 0.25,
    "comparable": 0.20,
    "expert": 0.10,
    "news": 0.10,
}


@dataclass(frozen=True)
class WeightedFairValue:
    fair_value: float | None
    original_weights: dict[str, float]
    effective_weights: dict[str, float]
    missing_components: list[str]
    warnings: list[str]
    needs_human_review: bool


def _prob(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def validate_weights(weights: Mapping[str, float], tolerance: float = 1e-6) -> dict[str, float]:
    normalized = {k: float(weights.get(k, 0.0)) for k in COMPONENT_TYPES}
    if any(v < 0 for v in normalized.values()):
        raise ValueError("Weights cannot be negative")
    total = sum(normalized.values())
    if abs(total - 1.0) > tolerance:
        raise ValueError(f"Weights must total 1.0, got {total:.6f}")
    return normalized


def compute_weighted_fair_value(
    components: Mapping[str, float | None],
    weights: Mapping[str, float] | None = None,
    redistribution_eligible: Mapping[str, bool] | None = None,
) -> WeightedFairValue:
    original = validate_weights(weights or DEFAULT_WEIGHTS)
    eligible = {k: True for k in COMPONENT_TYPES}
    if redistribution_eligible:
        eligible.update({k: bool(v) for k, v in redistribution_eligible.items() if k in eligible})

    values = {k: _prob(components.get(k), k) for k in COMPONENT_TYPES}
    missing = [k for k, v in values.items() if v is None]
    available_values: dict[str, float] = {k: v for k, v in values.items() if v is not None}
    available = list(available_values)
    if not available:
        return WeightedFairValue(None, original, {}, missing, ["No fair-value components are available."], True)

    non_redistributable_missing_weight = sum(original[k] for k in missing if not eligible[k])
    available_weight = sum(original[k] for k in available)
    if available_weight <= 0:
        return WeightedFairValue(None, original, {}, missing, ["Available components have zero total weight."], True)

    # Missing eligible weight is redistributed proportionally across available components.
    distributable_total = 1.0 - non_redistributable_missing_weight
    effective = {k: (original[k] / available_weight) * distributable_total for k in available}
    fair = sum(available_values[k] * effective[k] for k in available)
    # If a non-redistributable component is absent, the result is incomplete and intentionally not rescaled to 100%.
    if non_redistributable_missing_weight > 0:
        if distributable_total <= 0:
            return WeightedFairValue(
                None, original, effective, missing, ["No distributable component weight remains."], True
            )
        fair = fair / distributable_total

    warnings: list[str] = []
    if missing:
        warnings.append("Missing components: " + ", ".join(missing))
    if non_redistributable_missing_weight > 0:
        warnings.append("One or more missing components are not eligible for redistribution.")
    needs_review = bool(missing) or non_redistributable_missing_weight > 0
    return WeightedFairValue(fair, original, effective, missing, warnings, needs_review)


def partisan_premium(market_probability: float | None, fair_value: float | None) -> float | None:
    if market_probability is None or fair_value is None:
        return None
    return float(market_probability) - float(fair_value)


def brier_score(probability: float, outcome: float) -> float:
    if not 0 <= probability <= 1 or outcome not in (0, 1, 0.0, 1.0):
        raise ValueError("Probability must be [0,1] and outcome must be binary")
    return (float(probability) - float(outcome)) ** 2
