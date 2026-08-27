"""Resolved-outcome scoring, calibration, lead/lag, and backtesting (spec sections 34-36, 47, 49).

When a race resolves, every forecasting series -- ``market`` / ``quant`` / ``openai`` /
``anthropic`` / ``ensemble`` / ``legacy_llm`` -- is scored against the binary outcome with a
Brier score, at each standard horizon (90/60/30/14/7/1 days before the election), using the
observation nearest each horizon **without using any information from after it**. Results land in
``forecast_scores``; ``app.eval.calibration`` aggregates them (always reporting N).

``app.eval.backtest`` re-runs PPI Quant at past point-in-time cutoffs behind a provider that
**refuses** any datum dated after the cutoff -- no lookahead: a 30-days-before forecast can never
see a later poll, the final margin, later candidate info, or the resolution.
"""

from app.eval.backtest import PointInTimeError, run_backtest
from app.eval.calibration import build_calibration_report
from app.eval.metrics import STANDARD_HORIZONS, brier, log_loss
from app.eval.scorer import score_resolved_race

__all__ = [
    "PointInTimeError",
    "STANDARD_HORIZONS",
    "brier",
    "build_calibration_report",
    "log_loss",
    "run_backtest",
    "score_resolved_race",
]
