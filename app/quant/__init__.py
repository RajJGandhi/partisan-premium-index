"""PPI Quant -- deterministic quantitative election-forecasting engine.

This package implements the ``structured political data -> quantitative election model ->
probability distribution -> fair value`` path described in ``docs/research/PPI_QUANT_V1.md``.
It is deliberately self-contained and has **no dependency on any prediction-market data**:
nothing in ``app.quant`` imports ``app.ppi.polymarket``, reads ``market_snapshots``, or accepts
a market price / bid / ask / midpoint / spread / volume / liquidity argument. That separation is
enforced by ``tests/test_quant_market_independence.py``.

The engine is a pipeline of small pure functions:

    polls          -> app.quant.polling            -> polling_margin, n_eff
    history        -> app.quant.state_lean          -> state_lean
    generic ballot -> app.quant.national_environment-> national_environment
    the above      -> app.quant.fundamentals        -> fundamental_margin
    polling + fund -> app.quant.blend               -> expected_margin (mu)
    counts + time  -> app.quant.uncertainty         -> sigma_total
    mu, sigma      -> app.quant.probability         -> P(Dem win) via Phi(mu / sigma)

``app.quant.engine.run_quant_forecast`` wires them together; ``app.quant.adapters`` maps a
Polymarket contract type onto a forecasting adapter (``statewide_race`` is supported in v1;
``senate_control`` is experimental; ``house_control`` is unavailable; anything else abstains).
"""

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.engine import run_quant_forecast
from app.quant.types import QuantForecastInput, QuantForecastResult

__all__ = [
    "QUANT_V1",
    "MethodologyConfig",
    "QuantForecastInput",
    "QuantForecastResult",
    "run_quant_forecast",
]
