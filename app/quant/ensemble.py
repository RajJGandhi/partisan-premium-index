"""PPI Ensemble + robustness (spec sections 25, 27).

    PPI_ensemble = 0.60 * Quant + 0.20 * GPT_blind + 0.20 * Claude_blind

Weights are **predeclared** in :data:`app.quant.config.MethodologyConfig.ensemble_weights` and are
not re-fit per run. If any required component is missing the ensemble is reported as *unavailable*
-- the remaining components are **never silently reweighted** (that would be methodology drift).
Performance-weighted ensembling is a future methodology version, not this one.

Robustness answers "is a market/model gap real signal, or just PPI's own models disagreeing?":

    HIGH   -- |market - ensemble| >= 10 pts AND max pairwise model disagreement <= 8 pts
    MEDIUM -- meaningful market divergence, moderate model disagreement (<= 15 pts)
    LOW    -- the gap is driven mainly by disagreement among Quant / GPT / Claude
"""

from __future__ import annotations

import statistics
from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.types import EnsembleResult

_REQUIRED = ("quant", "openai", "anthropic")


def _pct_points(p: float) -> float:
    return 100.0 * p


def model_dispersion(values: list[float]) -> float:
    """Population standard deviation of the model probabilities (in probability units)."""
    if len(values) < 2:
        return 0.0
    return statistics.pstdev(values)


def max_pairwise_disagreement(values: list[float]) -> float:
    """Largest absolute gap between any two model probabilities (probability units)."""
    if len(values) < 2:
        return 0.0
    return max(abs(a - b) for i, a in enumerate(values) for b in values[i + 1 :])


def robustness_band(
    *,
    market_probability: Optional[float],
    ensemble_probability: float,
    max_pairwise: float,
    cfg: MethodologyConfig = QUANT_V1,
) -> Optional[str]:
    """Robustness label. Needs a market probability for context; returns ``None`` without one.

    NOTE: this is the *only* place in :mod:`app.quant` that reads a market probability, and it runs
    strictly after the ensemble has been computed and (in the pipeline) persisted -- it never feeds
    back into any forecast. Kept here, not in the engine, on purpose.
    """
    if market_probability is None:
        return None
    gap_pts = abs(_pct_points(market_probability) - _pct_points(ensemble_probability))
    spread_pts = _pct_points(max_pairwise)
    if gap_pts >= cfg.robustness_high_market_gap_pts and spread_pts <= cfg.robustness_high_max_pairwise_pts:
        return "HIGH"
    if spread_pts <= cfg.robustness_medium_max_pairwise_pts:
        return "MEDIUM"
    return "LOW"


def combine_ensemble(
    *,
    quant: Optional[float],
    openai: Optional[float],
    anthropic: Optional[float],
    market_probability: Optional[float] = None,
    cfg: MethodologyConfig = QUANT_V1,
) -> EnsembleResult:
    """Blend the three blind probabilities with predeclared weights, or report unavailable."""
    components: dict[str, Optional[float]] = {"quant": quant, "openai": openai, "anthropic": anthropic}
    missing = [k for k in _REQUIRED if components[k] is None]

    weights = dict(cfg.ensemble_weights)
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"ensemble_weights must sum to 1.0, got {sum(weights.values())}")

    if missing:
        return EnsembleResult(
            available=False,
            ensemble_probability=None,
            weights=weights,
            components=components,
            dispersion=None,
            max_pairwise_disagreement=None,
            robustness=None,
            unavailable_reason=f"ensemble unavailable -- missing component(s): {', '.join(missing)} "
            f"(components are NOT reweighted when one is absent)",
        )

    vals = [float(quant), float(openai), float(anthropic)]  # type: ignore[arg-type]
    ensemble_p = (
        weights["quant"] * vals[0] + weights["openai"] * vals[1] + weights["anthropic"] * vals[2]
    )
    disp = model_dispersion(vals)
    max_pair = max_pairwise_disagreement(vals)
    robustness = robustness_band(
        market_probability=market_probability,
        ensemble_probability=ensemble_p,
        max_pairwise=max_pair,
        cfg=cfg,
    )
    return EnsembleResult(
        available=True,
        ensemble_probability=ensemble_p,
        weights=weights,
        components=components,
        dispersion=disp,
        max_pairwise_disagreement=max_pair,
        robustness=robustness,
        unavailable_reason=None,
    )
