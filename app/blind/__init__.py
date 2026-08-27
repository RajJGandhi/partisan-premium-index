"""Independent blind-LLM benchmark forecasts + the PPI v1.5 ensemble (spec sections 20-27).

After the deterministic Quant forecast is complete and its immutable, market-free
``EvidenceBundle`` is built, two frontier LLMs each produce an **independent** probability from the
same evidence:

- **GPT blind forecast** (``app.blind.providers.OpenAIBlindProvider``) -- spec section 23
- **Claude blind forecast** (``app.blind.providers.AnthropicBlindProvider``) -- spec section 24

Neither sees a prediction-market price, the Quant probability, the other model's forecast, or the
ensemble result. Persisted append-only to ``blind_benchmark_forecasts`` (race-centric, separate
from the legacy ``llm_forecasts``). A missing key / SDK is an explicit ``SKIPPED_PROVIDER`` row.

``app.blind.ensemble_runner`` then combines Quant + GPT + Claude with the **predeclared** weights
``0.60 / 0.20 / 0.20`` (``app.quant.ensemble``). If any component is missing the ensemble is
recorded ``available = False`` -- the present components are never silently reweighted.

``app.blind.web_evidence`` collects contamination-filtered race news (spec sections 20, 21, 45)
for the ``EvidenceBundle`` -- displayed / stored / available to the blind forecasters, but never an
input to the deterministic Quant math.
"""

from app.blind.providers import (
    AnthropicBlindProvider,
    BlindForecastProvider,
    BlindLLMCall,
    DeterministicBlindProvider,
    OpenAIBlindProvider,
)
from app.blind.runner import run_blind_forecasts
from app.blind.schema import BlindForecastResponse

__all__ = [
    "AnthropicBlindProvider",
    "BlindForecastProvider",
    "BlindForecastResponse",
    "BlindLLMCall",
    "DeterministicBlindProvider",
    "OpenAIBlindProvider",
    "run_blind_forecasts",
]
