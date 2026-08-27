"""PPI automated political-data provider layer (spec sections 5, 6, 8-10, 21, 31, 42, 45).

Turns the Quant engine from "runs on seeded data" into "acquires its own data". Nothing here is
hard-wired: every source is a :class:`~app.providers.base.BaseProvider` behind an abstraction, and
providers of the same kind are composed into a :class:`~app.providers.base.ProviderChain` with an
explicit fallback order. Every fetch is cached, retried with backoff, validated, and -- on total
failure -- served from last-known-good with an explicit ``STALE`` status (missing is never zero).
Every operation records a ``data_provider_runs`` row and updates ``provider_health``.

Kinds:

- ``ElectionHistoryProvider``   -> historical_election_results   (state lean, spec 9)
- ``PollProvider``              -> poll_observations              (race polls, spec 6)
- ``GenericBallotProvider``     -> national_environment_observations (spec 10)
- ``CandidateProvider``         -> race_candidates / candidate_status_snapshots (spec 8)
- ``MarketDiscoveryProvider``   -> market classification          (spec 42)
- web evidence + contamination scanning                          (spec 21, 45)

The Quant engine still never sees any of the market data this layer also touches -- see
``app/quant`` and ``tests/test_quant_market_independence.py``.
"""

from app.providers.base import (
    BaseProvider,
    ProviderChain,
    ProviderError,
    ProviderResult,
)
from app.providers.contamination import (
    ContaminationResult,
    PredictionMarketContaminationScanner,
)

__all__ = [
    "BaseProvider",
    "ProviderChain",
    "ProviderError",
    "ProviderResult",
    "ContaminationResult",
    "PredictionMarketContaminationScanner",
]
