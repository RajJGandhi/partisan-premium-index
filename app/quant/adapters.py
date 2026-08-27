"""Forecasting-adapter architecture (spec section 4).

A Polymarket contract is mapped onto a ``ForecastAdapter``. v1 supports canonical quantitative
forecasting for U.S. Senate and governor general elections (``statewide_race``). Senate control
(``senate_control``) is implemented via Monte Carlo but marked *experimental* until the full
contested-race set is wired. House control (``house_control``) is deliberately **unavailable** --
PPI does not ship a fabricated district-level number. Anything else abstains.

The adapter never invents a forecast it cannot defend: unsupported / not-yet-wired contract types
return an ``AdapterOutcome`` with ``status="UNAVAILABLE"`` or ``"ABSTAIN"`` and a reason, and the
pipeline is expected to keep showing market price + blind LLM forecasts for those while suppressing
a Quant number.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.engine import run_quant_forecast
from app.quant.senate_control import SenateControlResult, simulate_senate_control
from app.quant.types import QuantForecastInput, QuantForecastResult

CONTRACT_STATEWIDE = "statewide_race"
CONTRACT_SENATE_CONTROL = "senate_control"
CONTRACT_HOUSE_CONTROL = "house_control"
CONTRACT_UNSUPPORTED = "unsupported"

SUPPORTED_STATUS = "SUPPORTED"
EXPERIMENTAL_STATUS = "EXPERIMENTAL"
UNAVAILABLE_STATUS = "UNAVAILABLE"
ABSTAIN_STATUS = "ABSTAIN"


@dataclass(frozen=True)
class AdapterOutcome:
    contract_type: str
    status: str  # SUPPORTED / EXPERIMENTAL / UNAVAILABLE / ABSTAIN
    quant_result: Optional[QuantForecastResult] = None
    senate_control_result: Optional[SenateControlResult] = None
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)


class ForecastAdapter(ABC):
    contract_type: str
    status: str

    @abstractmethod
    def forecast(self, *args, **kwargs) -> AdapterOutcome:  # pragma: no cover - interface
        ...


class StatewideRaceAdapter(ForecastAdapter):
    """U.S. Senate + governor general elections -- the canonical v1 quantitative adapter."""

    contract_type = CONTRACT_STATEWIDE
    status = SUPPORTED_STATUS

    def forecast(
        self, inp: QuantForecastInput, cfg: MethodologyConfig = QUANT_V1
    ) -> AdapterOutcome:
        result = run_quant_forecast(inp, cfg)
        status = ABSTAIN_STATUS if result.abstained else SUPPORTED_STATUS
        return AdapterOutcome(
            contract_type=self.contract_type,
            status=status,
            quant_result=result,
            reason="; ".join(result.abstain_reasons) if result.abstained else None,
        )


class SenateControlAdapter(ForecastAdapter):
    """Chamber control via Monte Carlo over individual Senate-race distributions (spec section 28).

    Marked EXPERIMENTAL: correct in mechanism, but only trustworthy once every contested race has a
    live Quant distribution. Returns UNAVAILABLE if handed an empty contested-race set.
    """

    contract_type = CONTRACT_SENATE_CONTROL
    status = EXPERIMENTAL_STATUS

    def forecast(
        self,
        contested,
        *,
        holdover_dem: int,
        holdover_rep: int,
        tie_break_party,
        n_sims: Optional[int] = None,
        seed: Optional[int] = None,
        correlated_national_error_sd: float = 0.0,
        cfg: MethodologyConfig = QUANT_V1,
    ) -> AdapterOutcome:
        contested = list(contested)
        if not contested:
            return AdapterOutcome(
                contract_type=self.contract_type,
                status=UNAVAILABLE_STATUS,
                reason="no contested Senate-race distributions supplied",
            )
        sim = simulate_senate_control(
            contested,
            holdover_dem=holdover_dem,
            holdover_rep=holdover_rep,
            tie_break_party=tie_break_party,
            n_sims=n_sims,
            seed=seed,
            correlated_national_error_sd=correlated_national_error_sd,
            cfg=cfg,
        )
        return AdapterOutcome(
            contract_type=self.contract_type,
            status=EXPERIMENTAL_STATUS,
            senate_control_result=sim,
            detail={"note": "experimental until the full contested-race set is wired"},
        )


class HouseControlAdapter(ForecastAdapter):
    """Deliberately unavailable -- PPI does not ship a fake House-control quant number (spec 29)."""

    contract_type = CONTRACT_HOUSE_CONTROL
    status = UNAVAILABLE_STATUS

    def forecast(self, *args, **kwargs) -> AdapterOutcome:
        return AdapterOutcome(
            contract_type=self.contract_type,
            status=UNAVAILABLE_STATUS,
            reason=(
                "House-control Quant is not implemented: a defensible district-level model "
                "(435-seat Monte Carlo with district baselines, generic ballot, incumbency and "
                "correlated national error) does not exist yet. Market price and blind LLM "
                "forecasts may still be displayed; Quant is suppressed."
            ),
        )


class UnsupportedAdapter(ForecastAdapter):
    """Any political contract PPI cannot map onto a defensible quantitative adapter -> abstain."""

    contract_type = CONTRACT_UNSUPPORTED
    status = ABSTAIN_STATUS

    def forecast(self, *args, **kwargs) -> AdapterOutcome:
        return AdapterOutcome(
            contract_type=self.contract_type,
            status=ABSTAIN_STATUS,
            reason="contract does not map onto a supported quantitative forecasting adapter",
        )


_REGISTRY: dict[str, ForecastAdapter] = {
    CONTRACT_STATEWIDE: StatewideRaceAdapter(),
    CONTRACT_SENATE_CONTROL: SenateControlAdapter(),
    CONTRACT_HOUSE_CONTROL: HouseControlAdapter(),
    CONTRACT_UNSUPPORTED: UnsupportedAdapter(),
}


def get_adapter(contract_type: str) -> ForecastAdapter:
    """Return the adapter for a contract type, falling back to :class:`UnsupportedAdapter`."""
    return _REGISTRY.get((contract_type or "").strip().lower(), _REGISTRY[CONTRACT_UNSUPPORTED])


def adapter_capabilities() -> dict[str, str]:
    """``{contract_type: status}`` -- surfaced on the System Status page."""
    return {ct: a.status for ct, a in _REGISTRY.items()}
