"""Persistence helpers shared by the v1.5 orchestrator and the shadow runner.

Kept in one place so the two entry points can never drift on how a ``QuantForecast`` /
``QuantEvidenceBundle`` / ``Race`` row is written. All writes are append-only (see
``app.quant.append_only``); an evidence bundle is write-once per content hash.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import QuantEvidenceBundle, QuantForecast, Race
from app.quant.append_only import upsert_quant_forecast
from app.quant.types import EvidenceBundle, QuantForecastInput, QuantForecastResult


def upsert_race(session: Session, inp: QuantForecastInput, *, source: str) -> Race:
    row = session.execute(select(Race).where(Race.race_id == inp.race.race_id)).scalar_one_or_none()
    if row is None:
        row = Race(race_id=inp.race.race_id)
        session.add(row)
    row.state = inp.race.state
    row.office = inp.race.office
    row.cycle = inp.race.cycle
    row.election_date = inp.race.election_date
    row.adapter_type = "statewide_race"
    row.dem_candidate_name = inp.race.dem_candidate.name if inp.race.dem_candidate else None
    row.rep_candidate_name = inp.race.rep_candidate.name if inp.race.rep_candidate else None
    row.incumbent_party = inp.race.incumbent_party
    row.source = source
    session.flush()
    return row


def persist_evidence_bundle(
    session: Session, bundle: EvidenceBundle, *, run_key: str, evidence_cutoff: datetime
) -> QuantEvidenceBundle:
    existing = session.execute(
        select(QuantEvidenceBundle).where(QuantEvidenceBundle.content_hash == bundle.content_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = QuantEvidenceBundle(
        race_id=bundle.race_id,
        run_key=run_key,
        forecast_timestamp=bundle.forecast_timestamp,
        election_date=bundle.election_date,
        evidence_cutoff=evidence_cutoff,
        payload_json=json.dumps(bundle.payload, sort_keys=True, separators=(",", ":"), default=str),
        content_hash=bundle.content_hash,
        source_manifest_json=json.dumps(bundle.payload.get("source_manifest", [])),
    )
    session.add(row)
    session.flush()
    return row


def persist_quant_forecast(
    session: Session,
    inp: QuantForecastInput,
    result: QuantForecastResult,
    bundle: EvidenceBundle,
    *,
    run_key: str,
    fair_value_yes: float | None = None,
    pipeline_mode: str = "shadow",
    publication_status: str = "SHADOW",
    job_run_id: int | None = None,
    race_source: str = "seed:quant_example_races",
) -> tuple[QuantForecast, bool]:
    """Write the Race + EvidenceBundle + QuantForecast for one race. Returns ``(row, created)``."""
    upsert_race(session, inp, source=race_source)
    eb = persist_evidence_bundle(session, bundle, run_key=run_key, evidence_cutoff=inp.as_of)
    u = result.uncertainty
    qf = QuantForecast(
        race_id=result.race_id,
        job_run_id=job_run_id,
        run_key=run_key,
        run_slot=run_key.split(":")[-1],
        adapter_type="statewide_race",
        methodology_version=result.methodology_version,
        config_hash=result.config_hash,
        evidence_bundle_id=eb.id,
        evidence_bundle_hash=bundle.content_hash,
        input_hash=result.input_hash,
        generated_at=result.generated_at,
        evidence_cutoff=inp.as_of,
        data_quality=result.data_quality,
        abstained=result.abstained,
        abstain_reasons_json=json.dumps(list(result.abstain_reasons)),
        polling_margin=result.polling_margin,
        fundamental_margin=result.fundamental_margin,
        poll_weight=result.poll_weight,
        expected_margin=result.expected_margin,
        sigma_total=None if u is None else u.sigma_total,
        sigma_time=None if u is None else u.sigma_time,
        sigma_polling=None if u is None else u.sigma_polling,
        sigma_office=None if u is None else u.sigma_office,
        sigma_status=None if u is None else u.sigma_status,
        p_dem_win=result.p_dem_win,
        p_dem_win_uncapped=result.p_dem_win_uncapped,
        p_rep_win=result.p_rep_win,
        fair_value_yes=fair_value_yes,
        n_eff=None if result.polling is None else result.polling.n_eff,
        used_poll_count=None if result.polling is None else result.polling.used_poll_count,
        latest_poll_date=None if result.polling is None else result.polling.latest_poll_date,
        state_lean=None if result.fundamentals is None else result.fundamentals.state_lean,
        national_environment=None if result.fundamentals is None else result.fundamentals.national_environment,
        incumbency_points=None if result.fundamentals is None else result.fundamentals.incumbency_adjustment,
        detail_json=json.dumps(result.detail, default=str),
        pipeline_mode=pipeline_mode,
        publication_status=publication_status,
    )
    row, created = upsert_quant_forecast(session, qf)
    return row, created
