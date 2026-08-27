"""The PPI v1.5 twice-daily pipeline -- the 10-stage orchestrator (spec section 41).

    1  discover        automatic Polymarket market discovery + classification -> races binding
    2  market snapshot  Polymarket Gamma/CLOB prices for the linked contracts
    3  political data   race polls / generic ballot / historical baselines / candidate status
    4  validate         dedupe + validate the structured inputs
    5  quant forecast   deterministic PPI Quant per race
    6  evidence bundle  timestamp-locked, market-free EvidenceBundle
    7  blind forecasts  GPT + Claude independently, from the bundle only
    8  ensemble         0.60 Quant + 0.20 GPT + 0.20 Claude (or "unavailable")
    9  comparison       join the persisted forecasts with the market snapshot -> market_model_spread
    10 publish          append the immutable observations to public history

There is no human approval gate. Each stage runs inside a SAVEPOINT so one race failing cannot
abort work already committed for the others. The headline series does **not** change here -- see
``app.pipeline_v15.cutover`` and ``docs/research/PPI_CUTOVER.md``.
"""

from app.pipeline_v15.orchestrator import PipelineSummary, run_v15_pipeline

__all__ = ["PipelineSummary", "run_v15_pipeline"]
