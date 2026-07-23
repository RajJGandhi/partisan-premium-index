from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiquidityScore:
    spread_score: int
    depth_score: int
    warnings: list[str]


def score_liquidity(spread: float | None, depth_3c: float | None) -> LiquidityScore:
    warnings: list[str] = []
    if spread is None:
        spread_score = -10
        warnings.append("Missing spread data.")
    elif spread <= 0.02:
        spread_score = 10
    elif spread <= 0.04:
        spread_score = 7
    elif spread <= 0.06:
        spread_score = 3
    else:
        spread_score = -10
        warnings.append("Wide spread.")

    if depth_3c is None:
        depth_score = -5
        warnings.append("Missing depth data.")
    elif depth_3c >= 1000:
        depth_score = 5
    elif depth_3c >= 250:
        depth_score = 3
    elif depth_3c < 100:
        depth_score = -5
        warnings.append("Low executable depth.")
    else:
        depth_score = 0
    return LiquidityScore(spread_score=spread_score, depth_score=depth_score, warnings=warnings)
