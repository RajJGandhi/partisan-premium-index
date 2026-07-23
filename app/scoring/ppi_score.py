from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.scoring.liquidity import score_liquidity


@dataclass(frozen=True)
class PPIInputs:
    polymarket_yes: float | None
    fair_yes: float | None
    emotional_side: str = "unclear"
    identity_intensity: int = 0
    institutional_friction: int = 0
    deadline_decay_relevance: int = 0
    end_date: datetime | None = None
    spread: float | None = None
    depth_3c: float | None = None
    resolution_risk: int = 3
    fair_value_confidence: float = 0.0


@dataclass(frozen=True)
class PPIResult:
    ppi_score: int
    premium: float | None
    action: str
    paper_side: str | None
    score_breakdown: dict[str, float | int | str | None]
    warnings: list[str]
    reject_reason: str | None = None


def clamp_score(value: float | int) -> int:
    return max(0, min(100, int(round(value))))


def premium_score(premium_abs: float | None) -> int:
    if premium_abs is None or premium_abs < 0.05:
        return 0
    if premium_abs < 0.08:
        return 20
    if premium_abs < 0.12:
        return 45
    if premium_abs < 0.20:
        return 70
    return 85


def map_identity(value: int) -> int:
    return {0: 0, 1: 3, 2: 6, 3: 9, 4: 12, 5: 15}.get(max(0, min(5, int(value))), 0)


def map_friction(value: int) -> int:
    return {0: 0, 1: 2, 2: 5, 3: 8, 4: 11, 5: 15}.get(max(0, min(5, int(value))), 0)


def days_to_end(end_date: datetime | None) -> int | None:
    if end_date is None:
        return None
    now = datetime.now(timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    return max(0, (end_date - now).days)


def deadline_score(deadline_decay_relevance: int, end_date: datetime | None) -> int:
    dte = days_to_end(end_date)
    if dte is None:
        return 0
    rel = max(0, min(5, int(deadline_decay_relevance)))
    if rel >= 4 and dte <= 14:
        return 10
    if rel >= 3 and dte <= 30:
        return 7
    if rel >= 2 and dte <= 60:
        return 4
    return 0


def resolution_penalty(resolution_risk: int) -> int:
    risk = max(0, min(5, int(resolution_risk)))
    return {0: 0, 1: -1, 2: -3, 3: -8, 4: -15, 5: -100}.get(risk, -8)


def confidence_penalty(confidence: float) -> int:
    c = max(0.0, min(1.0, float(confidence or 0.0)))
    if c >= 0.80:
        return 0
    if c >= 0.60:
        return -5
    if c >= 0.40:
        return -12
    return -100


def action_from_score(score: int) -> str:
    if score >= 80:
        return "high-conviction paper signal"
    if score >= 65:
        return "alert"
    if score >= 50:
        return "watchlist"
    return "ignore"


def determine_paper_side(emotional_side: str, premium: float | None) -> str | None:
    if premium is None:
        return None
    side = (emotional_side or "unclear").upper()
    if side == "YES" and premium > 0:
        return "BUY_NO"
    if side == "NO" and premium < 0:
        return "BUY_YES"
    if side == "unclear".upper():
        return None
    return None


def compute_ppi(inputs: PPIInputs) -> PPIResult:
    warnings: list[str] = []
    reject_reason: str | None = None
    if inputs.polymarket_yes is None or inputs.fair_yes is None:
        return PPIResult(
            ppi_score=0,
            premium=None,
            action="research queue",
            paper_side=None,
            score_breakdown={},
            warnings=["Missing market price or fair value."],
            reject_reason="missing_price_or_fair_value",
        )
    if not 0 <= inputs.polymarket_yes <= 1 or not 0 <= inputs.fair_yes <= 1:
        raise ValueError("polymarket_yes and fair_yes must be between 0 and 1")
    premium = inputs.polymarket_yes - inputs.fair_yes
    premium_abs = abs(premium)
    if premium_abs >= 0.20:
        warnings.append("Large premium may reflect hidden information or contract mismatch.")

    liq = score_liquidity(inputs.spread, inputs.depth_3c)
    warnings.extend(liq.warnings)
    components = {
        "premium_score": premium_score(premium_abs),
        "identity_score": map_identity(inputs.identity_intensity),
        "institutional_friction_score": map_friction(inputs.institutional_friction),
        "deadline_decay_score": deadline_score(inputs.deadline_decay_relevance, inputs.end_date),
        "liquidity_score": liq.spread_score,
        "depth_score": liq.depth_score,
        "resolution_penalty": resolution_penalty(inputs.resolution_risk),
        "confidence_penalty": confidence_penalty(inputs.fair_value_confidence),
    }
    raw_score = sum(components.values())
    score = clamp_score(raw_score)

    if inputs.resolution_risk >= 5:
        reject_reason = "resolution_risk_5"
        warnings.append("Resolution risk is 5; paper trade rejected.")
    if inputs.fair_value_confidence < 0.40:
        reject_reason = reject_reason or "fair_value_confidence_below_0_40"
        warnings.append("Fair-value confidence is below research threshold.")

    action = action_from_score(score)
    paper_side = determine_paper_side(inputs.emotional_side, premium)
    breakdown = {
        **components,
        "raw_score": raw_score,
        "premium": premium,
        "premium_points": premium * 100,
        "days_to_end": days_to_end(inputs.end_date),
        "emotional_side": inputs.emotional_side,
    }
    return PPIResult(
        ppi_score=score,
        premium=premium,
        action=action,
        paper_side=paper_side,
        score_breakdown=breakdown,
        warnings=warnings,
        reject_reason=reject_reason,
    )


def should_create_paper_trade(result: PPIResult, inputs: PPIInputs) -> tuple[bool, str | None]:
    settings = get_settings()
    if result.reject_reason:
        return False, result.reject_reason
    if result.ppi_score < settings.min_ppi_paper_score:
        return False, "ppi_score_below_threshold"
    if result.premium is None or abs(result.premium) < settings.min_premium_for_paper_trade:
        return False, "premium_below_threshold"
    if inputs.spread is None or inputs.spread > settings.max_allowed_spread:
        return False, "spread_too_wide_or_missing"
    if inputs.resolution_risk > 3:
        return False, "resolution_risk_above_3"
    if inputs.fair_value_confidence < settings.min_fair_value_confidence:
        return False, "fair_value_confidence_below_threshold"
    if not result.paper_side:
        return False, "no_valid_paper_side"
    return True, None
