"""Timestamp-locked evidence bundle (spec section 22).

For every race + forecasting run an immutable :class:`app.quant.types.EvidenceBundle` is built
containing only information available at the forecast timestamp and **no market data** (no price,
bid, ask, midpoint, spread, volume, liquidity, or commentary). Its ``content_hash`` is stored on
every downstream forecast (Quant, and later the GPT/Claude blind forecasts, which are handed this
exact bundle).

``EvidenceBundle.build`` runs ``assert_market_free`` over the payload, so a market field can never
enter a bundle even by accident.
"""

from __future__ import annotations

from app.quant.types import (
    CandidateInfo,
    EvidenceBundle,
    QuantForecastInput,
    QuantForecastResult,
)


def _candidate_dict(c: CandidateInfo | None) -> dict | None:
    if c is None:
        return None
    return {
        "name": c.name,
        "party": c.party,
        "is_incumbent": c.is_incumbent,
        "status": c.status,
        "source": c.source,
    }


def build_quant_evidence_bundle(
    inp: QuantForecastInput,
    result: QuantForecastResult,
    *,
    current_news: list[dict] | None = None,
    source_manifest: list[dict] | None = None,
) -> EvidenceBundle:
    """Assemble the immutable bundle. ``current_news`` / ``source_manifest`` come from the
    (contamination-filtered) web-evidence worker; they are display/benchmark context only and do
    not feed the Quant math."""
    polls_payload = [
        {
            "poll_id": p.poll_id,
            "pollster": p.pollster,
            "start_date": p.start_date.isoformat() if p.start_date else None,
            "end_date": p.end_date.isoformat(),
            "sample_size": p.sample_size,
            "population": p.population,
            "pollster_grade": p.pollster_grade,
            "partisan_sponsor": p.partisan_sponsor,
            "internal": p.internal,
            "dem_pct": p.dem_pct,
            "rep_pct": p.rep_pct,
            "margin_dem": p.margin,
            "source": p.source,
        }
        for p in sorted(inp.polls, key=lambda x: (x.end_date, x.pollster))
    ]

    polling_average = None
    if result.polling is not None:
        polling_average = {
            "polling_margin": result.polling.polling_margin,
            "n_eff": result.polling.n_eff,
            "raw_poll_count": result.polling.raw_poll_count,
            "used_poll_count": result.polling.used_poll_count,
            "latest_poll_date": (
                result.polling.latest_poll_date.isoformat()
                if result.polling.latest_poll_date
                else None
            ),
            "average_poll_age_days": result.polling.average_poll_age_days,
            "pollster_diversity": result.polling.pollster_diversity,
            "per_poll_weights": list(result.polling.per_poll),
        }

    fundamentals_payload = None
    if result.fundamentals is not None:
        fundamentals_payload = {
            "fundamental_margin": result.fundamentals.fundamental_margin,
            "state_lean": result.fundamentals.state_lean,
            "national_environment": result.fundamentals.national_environment,
            "incumbency_adjustment": result.fundamentals.incumbency_adjustment,
            "incumbent_party": result.fundamentals.incumbent_party,
            "detail": result.fundamentals.detail,
        }

    payload = {
        "race": {
            "race_id": inp.race.race_id,
            "state": inp.race.state,
            "office": inp.race.office,
            "cycle": inp.race.cycle,
            "election_date": inp.race.election_date.isoformat(),
        },
        "forecast_timestamp": inp.as_of.isoformat(),
        "election_date": inp.race.election_date.isoformat(),
        "polls": polls_payload,
        "polling_average": polling_average,
        "state_history": {
            "state": inp.state_history.state if inp.state_history else inp.race.state,
            "state_lean_detail": result.detail.get("state_lean_detail"),
        },
        "national_environment": result.detail.get("national_environment_detail"),
        "candidate_metadata": {
            "dem": _candidate_dict(inp.race.dem_candidate),
            "rep": _candidate_dict(inp.race.rep_candidate),
            "mapping_confidence": inp.candidate_mapping_confidence,
        },
        "incumbency": {
            "incumbent_party": inp.race.incumbent_party,
            "points": result.fundamentals.incumbency_adjustment if result.fundamentals else 0.0,
        },
        "fundamentals": fundamentals_payload,
        "current_news": current_news or [],
        "source_manifest": source_manifest or [],
        "methodology_version": result.methodology_version,
        "config_hash": result.config_hash,
        "input_hash": result.input_hash,
    }

    return EvidenceBundle.build(
        race_id=inp.race.race_id,
        forecast_timestamp=inp.as_of,
        election_date=inp.race.election_date,
        payload=payload,
    )
