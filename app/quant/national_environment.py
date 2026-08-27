"""National political environment (spec section 10).

The current Democratic generic-ballot margin, computed from individual generic-ballot polls using
the same weighting framework as race polls (:mod:`app.quant.polling`):

    NationalEnvironment = weighted mean of (Dem_i - Rep_i) over generic-ballot polls

No prediction-market / betting data enters this calculation, ever. If no generic-ballot polls are
available a caller-supplied ``override`` (from a National-environment provider's own computation)
may be used instead; if neither is available the environment is ``None`` and the fundamentals
model treats it as absent (not zero).
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.polling import weighted_generic_ballot_average
from app.quant.types import GenericBallotPoll


def compute_national_environment(
    generic_ballot: Sequence[GenericBallotPoll],
    as_of: date,
    *,
    override: Optional[float] = None,
    cfg: MethodologyConfig = QUANT_V1,
) -> tuple[Optional[float], dict]:
    """Return ``(national_environment_points, detail)`` -- Democratic margin, points."""
    avg = weighted_generic_ballot_average(generic_ballot, as_of, cfg)
    if avg.polling_margin is not None:
        return avg.polling_margin, {
            "source": "generic_ballot_polls",
            "n_polls": avg.used_poll_count,
            "n_eff": avg.n_eff,
            "latest_poll_date": avg.latest_poll_date.isoformat() if avg.latest_poll_date else None,
            "value": avg.polling_margin,
        }
    if override is not None:
        return float(override), {"source": "provider_override", "value": float(override)}
    return None, {"source": None, "reason": "no generic-ballot polls and no override"}
