"""PPI Quant engine -- the deterministic statistical election forecast (spec section 51).

    structured political data -> quantitative election model -> probability distribution -> fair value

``run_quant_forecast`` takes exactly one argument of political data and a methodology config. It
has **no market-price parameter of any kind**; ``tests/test_quant_market_independence.py`` asserts
this via ``inspect.signature`` and by importing the whole ``app.quant`` package and checking it
never pulls in ``app.ppi.polymarket``.

There is no code path anywhere that says "Democrats seem favored, therefore 72%". The probability
falls out of ``Phi(mu / sigma_total)`` once the explicit assumptions have produced ``mu`` and
``sigma_total``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.quant.blend import expected_margin, resolve_poll_weight
from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.data_quality import QualitySignals, classify_data_quality
from app.quant.fundamentals import compute_fundamentals
from app.quant.national_environment import compute_national_environment
from app.quant.polling import weighted_polling_average
from app.quant.probability import margin_to_win_probability
from app.quant.state_lean import compute_state_lean
from app.quant.types import (
    QuantForecastInput,
    QuantForecastResult,
    assert_market_free,
)
from app.quant.uncertainty import total_sigma


def _abstain(
    inp: QuantForecastInput,
    cfg: MethodologyConfig,
    generated_at: datetime,
    reasons: list[str],
) -> QuantForecastResult:
    return QuantForecastResult(
        race_id=inp.race.race_id,
        methodology_version=cfg.version,
        config_hash=cfg.config_hash(),
        input_hash=inp.input_hash(),
        generated_at=generated_at,
        data_quality="ABSTAIN",
        abstained=True,
        abstain_reasons=tuple(reasons),
        polling_margin=None,
        fundamental_margin=None,
        poll_weight=0.0,
        expected_margin=None,
        uncertainty=None,
        p_dem_win=None,
        p_rep_win=None,
        p_dem_win_uncapped=None,
        polling=None,
        fundamentals=None,
        detail={"abstain_reasons": reasons},
    )


def run_quant_forecast(
    inp: QuantForecastInput,
    cfg: MethodologyConfig = QUANT_V1,
) -> QuantForecastResult:
    """Deterministic statewide-race forecast. Pure: same input + config -> byte-identical result
    (modulo ``generated_at``). Never raises on thin data -- it abstains with reasons instead."""
    # Hard guarantee: nothing market-derived reached this function.
    assert_market_free(
        {
            "national_environment_override": inp.national_environment_override,
            "notes": list(inp.notes),
        },
        path="run_quant_forecast.input",
    )
    generated_at = datetime.now(timezone.utc)
    office = inp.race.office

    # --- abstention gates (spec section 30) -------------------------------------------------------
    abstain_reasons: list[str] = []
    if inp.candidate_mapping_confidence < cfg.abstain_mapping_confidence_below:
        abstain_reasons.append(
            f"candidate mapping confidence {inp.candidate_mapping_confidence:.2f} < "
            f"abstain threshold {cfg.abstain_mapping_confidence_below:.2f}"
        )
    if inp.race.dem_candidate is None and inp.race.rep_candidate is None:
        abstain_reasons.append("cannot identify either contest side (no candidate metadata)")
    no_polls = not any(p.end_date <= inp.as_of_date for p in inp.polls)

    # --- state lean + national environment ------------------------------------------------------
    state_lean, lean_detail = compute_state_lean(inp.state_history, cfg)
    national_environment, ne_detail = compute_national_environment(
        inp.generic_ballot,
        inp.as_of_date,
        override=inp.national_environment_override,
        cfg=cfg,
    )

    if no_polls and state_lean is None and national_environment is None:
        abstain_reasons.append("no usable polls, no state history, no national environment")

    if abstain_reasons:
        return _abstain(inp, cfg, generated_at, abstain_reasons)

    # --- polling average -----------------------------------------------------------------------
    polling = weighted_polling_average(inp.polls, inp.as_of_date, cfg)

    # --- fundamentals ------------------------------------------------------------------------
    fundamentals = compute_fundamentals(
        office=office,
        state_lean=state_lean,
        national_environment=national_environment,
        incumbent_party=inp.race.incumbent_party,
        cfg=cfg,
    )

    # --- blend -----------------------------------------------------------------------------
    alpha, alpha_detail = resolve_poll_weight(
        n_eff=polling.n_eff,
        days_to_election=inp.days_to_election,
        newest_poll_age_days=(
            None
            if polling.latest_poll_date is None
            else max(0.0, (inp.as_of_date - polling.latest_poll_date).days)
        ),
        has_usable_polls=polling.polling_margin is not None,
        has_fundamentals=fundamentals.fundamental_margin is not None,
        cfg=cfg,
    )
    mu = expected_margin(alpha, polling.polling_margin, fundamentals.fundamental_margin)

    if mu is None:
        return _abstain(
            inp, cfg, generated_at, ["neither a polling margin nor a fundamental margin could be computed"]
        )

    # --- uncertainty ----------------------------------------------------------------------
    uncertainty = total_sigma(
        days_to_election=inp.days_to_election,
        n_eff=polling.n_eff,
        office=office,
        nominees_confirmed=inp.race.nominees_confirmed,
        candidate_mapping_confidence=inp.candidate_mapping_confidence,
        cfg=cfg,
    )

    # --- margin -> probability (the only probability-space step) --------------------------------
    prob = margin_to_win_probability(mu, uncertainty.sigma_total, cfg)

    # --- data-quality label ----------------------------------------------------------------
    newest_age = (
        None
        if polling.latest_poll_date is None
        else max(0.0, (inp.as_of_date - polling.latest_poll_date).days)
    )
    quality, quality_reasons = classify_data_quality(
        QualitySignals(
            used_poll_count=polling.used_poll_count,
            n_eff=polling.n_eff,
            pollster_diversity=polling.pollster_diversity,
            newest_poll_age_days=newest_age,
            has_state_lean=state_lean is not None,
            has_national_environment=national_environment is not None,
            national_environment_stale=inp.national_environment_stale,
            nominees_confirmed=inp.race.nominees_confirmed,
            candidate_mapping_confidence=inp.candidate_mapping_confidence,
            provider_degraded=inp.provider_degraded,
        ),
        cfg,
    )

    return QuantForecastResult(
        race_id=inp.race.race_id,
        methodology_version=cfg.version,
        config_hash=cfg.config_hash(),
        input_hash=inp.input_hash(),
        generated_at=generated_at,
        data_quality=quality,
        abstained=False,
        abstain_reasons=(),
        polling_margin=polling.polling_margin,
        fundamental_margin=fundamentals.fundamental_margin,
        poll_weight=alpha,
        expected_margin=mu,
        uncertainty=uncertainty,
        p_dem_win=prob["p_dem_win"],
        p_rep_win=prob["p_rep_win"],
        p_dem_win_uncapped=prob["p_dem_win_uncapped"],
        polling=polling,
        fundamentals=fundamentals,
        detail={
            "quality_reasons": quality_reasons,
            "state_lean_detail": lean_detail,
            "national_environment_detail": ne_detail,
            "fundamentals_detail": fundamentals.detail,
            "alpha_detail": alpha_detail,
            "z": prob["z"],
            "days_to_election": inp.days_to_election,
        },
    )
