"""Historical state partisan lean (spec section 9).

For each election year y:

    StateLean_y = StatePresidentialMargin_y - NationalPresidentialMargin_y   (Democratic margin)

and the current lean is the weighted sum over the configured years:

    StateLean = sum_y weight_y * StateLean_y      (default 2016/2020/2024 = 0.15/0.30/0.55)

Everything is in Democratic-minus-Republican points (D+5 -> +5, R+5 -> -5). If a year is missing
from either the state or national series, its weight is redistributed proportionally across the
years that are present, and that is reported in the detail dict.
"""

from __future__ import annotations

from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.types import StateHistory


def compute_state_lean(
    history: Optional[StateHistory],
    cfg: MethodologyConfig = QUANT_V1,
) -> tuple[Optional[float], dict]:
    """Return ``(state_lean_points, detail)``. ``state_lean_points`` is ``None`` if no year usable."""
    detail: dict = {"years": {}, "weights_used": {}, "missing_years": []}
    if history is None:
        detail["reason"] = "no state history provided"
        return None, detail

    per_year: dict[str, float] = {}
    for year_str in cfg.state_lean_weights:
        year = int(year_str)
        state_r = history.state_results.get(year)
        nat_r = history.national_results.get(year)
        if state_r is None or nat_r is None:
            detail["missing_years"].append(year)
            continue
        lean_y = state_r.margin - nat_r.margin
        per_year[year_str] = lean_y
        detail["years"][year_str] = {
            "state_margin": state_r.margin,
            "national_margin": nat_r.margin,
            "lean": lean_y,
        }

    if not per_year:
        detail["reason"] = "no overlapping state+national results for any configured year"
        return None, detail

    total_present_weight = sum(cfg.state_lean_weights[y] for y in per_year)
    state_lean = 0.0
    for year_str, lean_y in per_year.items():
        effective = cfg.state_lean_weights[year_str] / total_present_weight
        detail["weights_used"][year_str] = effective
        state_lean += effective * lean_y

    detail["state_lean"] = state_lean
    detail["redistributed"] = bool(detail["missing_years"])
    return state_lean, detail
