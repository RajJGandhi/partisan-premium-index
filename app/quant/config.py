"""Versioned methodology configuration for PPI Quant.

Every magic number in the quantitative model lives here, in one frozen, hashable object -- not
scattered through the calculation modules. Changing any constant below is a *methodology change*:
it requires bumping ``version`` (and recording the change in ``docs/research/PPI_QUANT_V1.md`` and
the ``methodology_versions`` table), because every stored ``quant_forecasts`` row is stamped with
the ``version`` and ``config_hash`` that produced it.

All v1.0 values are **provisional**. They are transparent, defensible starting points, not
backtested optima -- see ``PROVISIONAL_PARAMETERS`` and section 48 of the PPI v1.5 spec. Later
methodology versions are expected to estimate these from out-of-sample performance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Mapping

METHODOLOGY_VERSION = "ppi-quant-v1.0"
ENSEMBLE_METHODOLOGY_VERSION = "ppi-ensemble-v1.5"

# Marker stamped onto pre-rewrite blind-LLM forecasts so the legacy series stays clearly labelled
# and is never confused with the new deterministic Quant series. See migrations note in
# scripts/migrate_db.py (llm_forecasts.methodology_version / forecast_role).
LEGACY_BLIND_LLM_METHODOLOGY_VERSION = "ppi-v0-legacy-blind-llm"


@dataclass(frozen=True)
class MethodologyConfig:
    """Immutable bundle of every tunable constant in the Quant model.

    Construct the canonical instance once (``QUANT_V1`` below) and thread it through the engine.
    ``frozen=True`` plus tuple-typed sequences make instances effectively hashable snapshots; use
    :meth:`config_hash` for the stable content hash persisted alongside each forecast.
    """

    version: str = METHODOLOGY_VERSION

    # --- Section 9: historical state partisan lean -------------------------------------------------
    # StateLean = sum_y weight_y * (StatePresMargin_y - NationalPresMargin_y), Dem-minus-Rep.
    state_lean_weights: Mapping[str, float] = field(
        default_factory=lambda: {"2016": 0.15, "2020": 0.30, "2024": 0.55}
    )

    # --- Section 11: polling weights -------------------------------------------------------------
    poll_half_life_days: float = 21.0  # Recency_i = 0.5 ** (age_days / half_life)
    sample_size_reference_n: float = 600.0  # Sample_i = sqrt(N / reference_n)
    sample_weight_cap_n: float = 5000.0  # giant polls capped at sqrt(cap_n / reference_n)
    sample_weight_floor_n: float = 100.0  # tiny polls floored at sqrt(floor_n / reference_n)

    population_weights: Mapping[str, float] = field(
        default_factory=lambda: {"LV": 1.00, "RV": 0.90, "A": 0.75, "UNKNOWN": 0.85}
    )
    pollster_grade_weights: Mapping[str, float] = field(
        default_factory=lambda: {"A": 1.10, "B": 1.00, "C": 0.85, "UNKNOWN": 0.90}
    )
    sponsor_weights: Mapping[str, float] = field(
        default_factory=lambda: {"PUBLIC": 1.00, "PARTISAN": 0.80, "INTERNAL": 0.75}
    )

    # Pollster flooding: within the window, a given pollster's k-th most recent poll (k = 0 for the
    # newest) is multiplied by ``decay ** k`` so five near-duplicate releases cannot dominate.
    pollster_flooding_window_days: float = 14.0
    pollster_flooding_decay: float = 0.5

    # --- Section 13: fundamentals -------------------------------------------------------------
    senate_incumbency_bonus: float = 1.5  # Dem incumbent +X, Rep incumbent -X, open seat 0
    governor_incumbency_bonus: float = 2.0
    governor_generic_ballot_multiplier: float = 0.65  # governor races are less nationalised

    # --- Section 14: blend polling with fundamentals -----------------------------------------
    # BaseAlpha = 1 - e^(-n_eff / alpha_neff_scale)
    alpha_neff_scale: float = 2.5
    # Time-to-election caps on alpha (days_before_election -> max alpha).
    alpha_time_caps: tuple[tuple[float, float], ...] = (
        (180.0, 0.65),
        (90.0, 0.75),
        (30.0, 0.88),
        (0.0, 0.93),
    )
    # Stale-polling multiplier on alpha, keyed by the age in days of the newest usable poll.
    alpha_staleness_steps: tuple[tuple[float, float], ...] = (
        (30.0, 1.00),
        (60.0, 0.65),
    )
    alpha_staleness_beyond_last_step: float = 0.35  # newest poll older than the last step

    # --- Section 15: uncertainty model -----------------------------------------------------------
    # sigma_time schedule: (days_before_election, sigma_points). Interpolated continuously.
    sigma_time_schedule: tuple[tuple[float, float], ...] = (
        (365.0, 9.0),
        (180.0, 8.0),
        (120.0, 7.0),
        (90.0, 6.3),
        (60.0, 5.5),
        (30.0, 4.8),
        (14.0, 4.2),
        (7.0, 3.8),
        (1.0, 3.3),
    )
    # sigma_polling additive term keyed by effective poll count (n_eff >= key -> value).
    sigma_polling_steps: tuple[tuple[float, float], ...] = (
        (7.0, 0.0),
        (4.0, 0.5),
        (2.0, 1.25),
        (1.0, 2.5),
        (0.0, 4.0),
    )
    sigma_office: Mapping[str, float] = field(
        default_factory=lambda: {"senate": 0.0, "governor": 0.75}
    )
    # sigma_status: added when nominee/candidate identity is unresolved but not fatal.
    sigma_status_unconfirmed_nominee: float = 2.0
    sigma_status_low_mapping_confidence: float = 1.5
    # Below this candidate-mapping confidence the engine abstains rather than adding sigma.
    abstain_mapping_confidence_below: float = 0.60

    # --- Section 16: margin -> probability -----------------------------------------------------
    probability_floor: float = 0.005
    probability_ceiling: float = 0.995

    # --- Section 25 / 27: ensemble + robustness --------------------------------------------------
    ensemble_weights: Mapping[str, float] = field(
        default_factory=lambda: {"quant": 0.60, "openai": 0.20, "anthropic": 0.20}
    )
    robustness_high_market_gap_pts: float = 10.0  # |market - ensemble| >= this ...
    robustness_high_max_pairwise_pts: float = 8.0  # ... AND model spread <= this -> HIGH
    robustness_medium_max_pairwise_pts: float = 15.0

    # --- Section 28: Senate-control Monte Carlo ------------------------------------------------
    senate_control_default_sims: int = 50_000
    senate_control_default_seed: int = 20260826

    def as_dict(self) -> dict:
        """Plain-dict view with deterministic ordering, suitable for JSON persistence."""
        return _canonicalise(asdict(self))

    def config_hash(self) -> str:
        """Stable SHA-256 of the canonicalised config -- stored on every quant_forecasts row."""
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- lookups (kept here so callers never hardcode a fallback key) --------------------------
    def population_weight(self, population: str | None) -> float:
        key = (population or "UNKNOWN").strip().upper()
        return float(self.population_weights.get(key, self.population_weights["UNKNOWN"]))

    def grade_weight(self, grade: str | None) -> float:
        key = _grade_bucket(grade)
        return float(self.pollster_grade_weights.get(key, self.pollster_grade_weights["UNKNOWN"]))

    def sponsor_weight(self, *, partisan_sponsor: str | None, internal: bool) -> float:
        if internal:
            return float(self.sponsor_weights["INTERNAL"])
        if partisan_sponsor:
            return float(self.sponsor_weights["PARTISAN"])
        return float(self.sponsor_weights["PUBLIC"])

    def office_sigma(self, office: str) -> float:
        return float(self.sigma_office.get(office.strip().lower(), 0.0))

    def incumbency_bonus(self, office: str) -> float:
        return (
            self.governor_incumbency_bonus
            if office.strip().lower() == "governor"
            else self.senate_incumbency_bonus
        )


def _grade_bucket(grade: str | None) -> str:
    if not grade:
        return "UNKNOWN"
    first = grade.strip()[:1].upper()
    if first in {"A", "B", "C"}:
        return first
    if first in {"D", "F"}:
        return "C"  # treat D/F pollster grades as the lowest defined bucket
    return "UNKNOWN"


def _canonicalise(obj):
    if isinstance(obj, dict):
        return {k: _canonicalise(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_canonicalise(v) for v in obj]
    return obj


# The single canonical v1.0 instance. Import this; do not build ad hoc configs in production code.
QUANT_V1 = MethodologyConfig()


# Human-facing catalogue of which constants are explicitly provisional (spec section 48). Surfaced
# on the public methodology page so a reader knows exactly what has not yet been backtested.
PROVISIONAL_PARAMETERS: tuple[str, ...] = (
    "poll_half_life_days (21d exponential decay)",
    "state_lean_weights (2016/2020/2024 = 0.15/0.30/0.55)",
    "sample_size_reference_n / caps (sqrt(N/600), capped)",
    "population_weights (LV/RV/A/Unknown = 1.00/0.90/0.75/0.85)",
    "pollster_grade_weights (A/B/C/Unknown = 1.10/1.00/0.85/0.90)",
    "sponsor_weights (public/partisan/internal = 1.00/0.80/0.75)",
    "pollster_flooding (14d window, 0.5 geometric decay)",
    "senate_incumbency_bonus / governor_incumbency_bonus (+/-1.5, +/-2.0)",
    "governor_generic_ballot_multiplier (0.65)",
    "alpha_neff_scale (1 - e^(-n_eff/2.5)) and alpha_time_caps",
    "alpha_staleness_steps (<=30d none, 31-60d x0.65, >60d x0.35)",
    "sigma_time_schedule (365d..1d = 9.0..3.3 pts)",
    "sigma_polling_steps (n_eff>=7 -> 0 ... n_eff=0 -> 4.0 pts)",
    "sigma_office (senate 0, governor 0.75)",
    "sigma_status terms and abstain_mapping_confidence_below (0.60)",
    "probability_floor / probability_ceiling (0.5% / 99.5%)",
    "ensemble_weights (quant/openai/anthropic = 0.60/0.20/0.20)",
    "robustness thresholds (HIGH: gap>=10pt & spread<=8pt)",
)
