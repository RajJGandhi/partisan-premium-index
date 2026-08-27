"""Data-quality classification (spec section 30).

Every quantitative forecast is stamped with one of:

    STRONG   -- several recent polls, multiple pollsters, known nominees, current generic ballot,
                clean candidate metadata
    NORMAL   -- some polling, fundamentals available
    THIN     -- one poll or stale polling; fundamentals dominate
    DEGRADED -- provider failure, stale national environment, or candidate ambiguity
    ABSTAIN  -- race cannot be forecast responsibly (handled by the engine before this runs)

PPI prefers an explicit abstention to fake precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig


@dataclass(frozen=True)
class QualitySignals:
    used_poll_count: int
    n_eff: float
    pollster_diversity: int
    newest_poll_age_days: Optional[float]
    has_state_lean: bool
    has_national_environment: bool
    national_environment_stale: bool
    nominees_confirmed: bool
    candidate_mapping_confidence: float
    provider_degraded: bool


def classify_data_quality(sig: QualitySignals, cfg: MethodologyConfig = QUANT_V1) -> tuple[str, list[str]]:
    """Return ``(label, reasons)``. Never returns ABSTAIN -- that is the engine's decision."""
    reasons: list[str] = []

    degraded = False
    if sig.provider_degraded:
        degraded = True
        reasons.append("a data provider is degraded / on fallback")
    if sig.national_environment_stale:
        degraded = True
        reasons.append("national environment (generic ballot) is stale")
    if cfg.abstain_mapping_confidence_below <= sig.candidate_mapping_confidence < 0.85:
        degraded = True
        reasons.append(f"candidate mapping confidence low ({sig.candidate_mapping_confidence:.2f})")
    if degraded:
        return "DEGRADED", reasons

    stale_polls = sig.newest_poll_age_days is not None and sig.newest_poll_age_days > 60
    if sig.used_poll_count <= 1 or stale_polls:
        if sig.used_poll_count == 0:
            reasons.append("no usable polls; fundamentals-only forecast")
        elif stale_polls:
            reasons.append(f"newest poll is {sig.newest_poll_age_days:.0f} days old")
        else:
            reasons.append("only one usable poll")
        return "THIN", reasons

    strong = (
        sig.used_poll_count >= 4
        and sig.n_eff >= 3.0
        and sig.pollster_diversity >= 3
        and sig.nominees_confirmed
        and sig.has_state_lean
        and sig.has_national_environment
        and sig.candidate_mapping_confidence >= 0.85
        and (sig.newest_poll_age_days is None or sig.newest_poll_age_days <= 21)
    )
    if strong:
        return "STRONG", ["several recent polls, multiple pollsters, clean fundamentals + metadata"]

    reasons.append("some polling plus fundamentals available")
    return "NORMAL", reasons
