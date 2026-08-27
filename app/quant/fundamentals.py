"""Fundamentals model (spec section 13).

Senate:

    FundamentalMargin = StateLean + NationalEnvironment + Incumbency

Governor (less perfectly nationalised):

    FundamentalMargin = StateLean + 0.65 * NationalEnvironment + Incumbency

Incumbency (Democratic-margin points, provisional v1):

    Senate   : Dem incumbent +1.5, Rep incumbent -1.5, open seat 0
    Governor : Dem incumbent +2.0, Rep incumbent -2.0, open seat 0

All terms are in Democratic-minus-Republican points. A missing NationalEnvironment is treated as
absent: the fundamental margin is still computed from StateLean + Incumbency and flagged, rather
than silently substituting zero.
"""

from __future__ import annotations

from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.types import Fundamentals, Office, Party


def incumbency_adjustment(
    office: Office,
    incumbent_party: Optional[Party],
    cfg: MethodologyConfig = QUANT_V1,
) -> float:
    """Signed Democratic-margin points for incumbency. Open seat -> 0.0."""
    if incumbent_party not in ("DEM", "REP"):
        return 0.0
    bonus = cfg.incumbency_bonus(office)
    return bonus if incumbent_party == "DEM" else -bonus


def compute_fundamentals(
    *,
    office: Office,
    state_lean: Optional[float],
    national_environment: Optional[float],
    incumbent_party: Optional[Party],
    cfg: MethodologyConfig = QUANT_V1,
) -> Fundamentals:
    inc = incumbency_adjustment(office, incumbent_party, cfg)
    ge_multiplier = cfg.governor_generic_ballot_multiplier if office == "governor" else 1.0

    detail: dict = {
        "office": office,
        "generic_ballot_multiplier": ge_multiplier,
        "incumbency_points": inc,
    }

    if state_lean is None:
        detail["reason"] = "state_lean unavailable -- fundamentals cannot be computed"
        return Fundamentals(
            fundamental_margin=None,
            state_lean=None,
            national_environment=national_environment,
            incumbency_adjustment=inc,
            incumbent_party=incumbent_party,
            detail=detail,
        )

    national_term = 0.0
    if national_environment is None:
        detail["national_environment_missing"] = True
    else:
        national_term = ge_multiplier * float(national_environment)
    detail["national_environment_term"] = national_term

    fundamental_margin = float(state_lean) + national_term + inc
    detail["fundamental_margin"] = fundamental_margin
    detail["formula"] = (
        "state_lean + national_environment + incumbency"
        if office == "senate"
        else "state_lean + 0.65*national_environment + incumbency"
    )

    return Fundamentals(
        fundamental_margin=fundamental_margin,
        state_lean=float(state_lean),
        national_environment=national_environment,
        incumbency_adjustment=inc,
        incumbent_party=incumbent_party,
        detail=detail,
    )
