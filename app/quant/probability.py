"""Convert an expected margin distribution into a win probability (spec section 16).

    P(DemWin) = Phi(mu / sigma_total)

where Phi is the standard normal CDF, implemented with ``math.erf`` (no SciPy dependency, fully
deterministic). The uncapped value is always retained; only the published value is clamped to
[probability_floor, probability_ceiling]. Nothing is rounded here -- rounding is for presentation.
"""

from __future__ import annotations

import math
from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig


def standard_normal_cdf(z: float) -> float:
    """Phi(z) via the error function. Phi(0) = 0.5, Phi(1.96) ~= 0.975."""
    return 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0)))


def margin_to_win_probability(
    mu: Optional[float],
    sigma_total: float,
    cfg: MethodologyConfig = QUANT_V1,
) -> dict[str, Optional[float]]:
    """Return ``{p_dem_win, p_dem_win_uncapped, p_rep_win, z}``.

    ``sigma_total`` must be > 0 (the uncertainty model never produces 0 -- ``sigma_time`` alone is
    always >= the smallest schedule point). If ``mu`` is ``None`` the result is all ``None``.
    """
    if mu is None:
        return {"p_dem_win": None, "p_dem_win_uncapped": None, "p_rep_win": None, "z": None}
    if sigma_total <= 0:
        raise ValueError("sigma_total must be positive; the uncertainty model should guarantee this")

    z = float(mu) / float(sigma_total)
    uncapped = standard_normal_cdf(z)
    capped = min(max(uncapped, cfg.probability_floor), cfg.probability_ceiling)
    return {
        "p_dem_win": capped,
        "p_dem_win_uncapped": uncapped,
        "p_rep_win": 1.0 - capped,
        "z": z,
    }
