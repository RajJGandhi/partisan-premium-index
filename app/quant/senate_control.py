"""Senate-control Monte Carlo (spec section 28).

Chamber control is built up from the individual Senate-race forecast distributions, not asked of
an LLM. For each simulation:

  1. draw a margin from every contested race's ``Normal(mu, sigma_total)``;
  2. convert to a seat winner (margin > 0 -> Democratic seat);
  3. add the holdover / not-up seats each party already holds;
  4. apply the tie-break rule (VP party wins a 50-50 chamber);
  5. tally control.

    P(control) = simulations_with_control / total_simulations

v1.2 treats races as independent. A later version adds a shared ``NationalShock`` term so a
national polling miss moves several states together -- see ``correlated_national_error`` below,
wired but defaulted off.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

from app.quant.config import QUANT_V1, MethodologyConfig

TieBreakParty = Literal["DEM", "REP"]


@dataclass(frozen=True)
class SenateRaceDistribution:
    race_id: str
    mu: float  # expected Democratic margin, points
    sigma_total: float  # points


@dataclass(frozen=True)
class SenateControlResult:
    p_dem_control: float
    p_rep_control: float
    n_sims: int
    seed: int
    tie_break_party: TieBreakParty
    holdover_dem: int
    holdover_rep: int
    contested: int
    seats_for_majority: int
    dem_seat_distribution: dict[int, float] = field(default_factory=dict)
    correlated_national_error_sd: float = 0.0


def simulate_senate_control(
    contested: Sequence[SenateRaceDistribution],
    *,
    holdover_dem: int,
    holdover_rep: int,
    tie_break_party: TieBreakParty,
    n_sims: Optional[int] = None,
    seed: Optional[int] = None,
    correlated_national_error_sd: float = 0.0,
    total_seats: int = 100,
    cfg: MethodologyConfig = QUANT_V1,
) -> SenateControlResult:
    """Run the chamber-control simulation. Deterministic given ``seed``.

    ``correlated_national_error_sd`` (points): if > 0, each simulation draws one shared shock added
    to every contested race's margin, so races move together. Default 0.0 == fully independent
    (v1.2 behaviour).
    """
    n = int(n_sims if n_sims is not None else cfg.senate_control_default_sims)
    s = int(seed if seed is not None else cfg.senate_control_default_seed)
    if n <= 0:
        raise ValueError("n_sims must be positive")
    if holdover_dem < 0 or holdover_rep < 0:
        raise ValueError("holdover seat counts must be non-negative")

    rng = random.Random(s)
    contested = list(contested)
    seats_for_majority = total_seats // 2 + 1  # 51 of 100
    tie_seats = total_seats // 2  # exact 50-50

    dem_control = 0
    dem_seat_counts: dict[int, int] = {}
    for _ in range(n):
        shock = rng.gauss(0.0, correlated_national_error_sd) if correlated_national_error_sd > 0 else 0.0
        dem_seats = holdover_dem
        for race in contested:
            margin = rng.gauss(race.mu, race.sigma_total) + shock
            if margin > 0:
                dem_seats += 1
        dem_seat_counts[dem_seats] = dem_seat_counts.get(dem_seats, 0) + 1
        # dem_seats >= 51 -> Dem control; == 50 -> VP's party (tie_break_party); else Rep control.
        if dem_seats >= seats_for_majority or (dem_seats == tie_seats and tie_break_party == "DEM"):
            dem_control += 1

    p_dem = dem_control / n
    return SenateControlResult(
        p_dem_control=p_dem,
        p_rep_control=1.0 - p_dem,
        n_sims=n,
        seed=s,
        tie_break_party=tie_break_party,
        holdover_dem=holdover_dem,
        holdover_rep=holdover_rep,
        contested=len(contested),
        seats_for_majority=seats_for_majority,
        dem_seat_distribution={k: v / n for k, v in sorted(dem_seat_counts.items())},
        correlated_national_error_sd=correlated_national_error_sd,
    )
