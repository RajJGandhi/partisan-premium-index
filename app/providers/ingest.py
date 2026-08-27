"""Ingestion orchestrator (spec sections 41 stages 3-4, 31).

Runs the provider chains for a set of races and writes the normalized, de-duplicated observations
into the append-only tables the Quant engine reads:

    election_history chain  -> historical_election_results   (upsert per jurisdiction/year/office)
    generic_ballot chain    -> national_environment_observations (dedup on content_hash)
    candidate chains        -> race_candidates + candidate_status_snapshots
    poll chain              -> poll_observations              (dedup on content_hash)

Every chain records a ``data_provider_runs`` row and updates ``provider_health`` (via
``ProviderChain``). Missing data is left missing -- never written as zero.

``build_quant_input_from_db`` then assembles a market-free ``QuantForecastInput`` straight from
those tables: the ``providers -> DB -> engine`` path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import (
    CandidateStatusSnapshot,
    HistoricalElectionResult,
    NationalEnvironmentObservation,
    PollObservation,
    Race,
    RaceCandidate,
)
from app.providers.candidates import CandidateRecord, default_candidate_chain
from app.providers.election_history import HistoricalResultRow, default_election_history_chain
from app.providers.national_environment import (
    default_generic_ballot_chain,
    generic_ballot_content_hash,
    partisan_fields,
)
from app.providers.normalize import canonical_office
from app.providers.polls import default_poll_chain, polls_to_observations
from app.providers.race_identity import KnownRace
from app.quant.types import (
    CandidateInfo,
    GenericBallotPoll,
    NormalizedPoll,
    PresidentialResult,
    QuantForecastInput,
    RaceMeta,
    StateHistory,
)

DEFAULT_LEAN_YEARS = (2016, 2020, 2024)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class IngestSummary:
    cycle: int
    races: int = 0
    history_rows: int = 0
    generic_ballot_rows: int = 0
    candidate_rows: int = 0
    poll_rows: int = 0
    poll_skipped: list[dict] = field(default_factory=list)
    chains: dict[str, dict] = field(default_factory=dict)  # kind -> {provider_used, fallback_reason, status}

    def as_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "races": self.races,
            "history_rows": self.history_rows,
            "generic_ballot_rows": self.generic_ballot_rows,
            "candidate_rows": self.candidate_rows,
            "poll_rows": self.poll_rows,
            "poll_skipped": self.poll_skipped,
            "chains": self.chains,
        }


def _known_races(race_configs: list[dict]) -> list[KnownRace]:
    out = []
    for r in race_configs:
        out.append(
            KnownRace(
                race_id=r["race_id"],
                state=r["state"],
                office=canonical_office(r["office"]),
                cycle=int(r["cycle"]),
                dem_candidate=(r.get("dem_candidate") or {}).get("name") if isinstance(r.get("dem_candidate"), dict) else r.get("dem_candidate"),
                rep_candidate=(r.get("rep_candidate") or {}).get("name") if isinstance(r.get("rep_candidate"), dict) else r.get("rep_candidate"),
            )
        )
    return out


def _upsert_race(session: Session, r: dict) -> None:
    row = session.execute(select(Race).where(Race.race_id == r["race_id"])).scalar_one_or_none()
    if row is None:
        row = Race(race_id=r["race_id"])
        session.add(row)
    row.state = r["state"].upper()[:2]
    row.office = canonical_office(r["office"])
    row.cycle = int(r["cycle"])
    row.election_date = _as_date(r.get("election_date")) or row.election_date or date(int(r["cycle"]), 11, 3)
    row.adapter_type = "statewide_race"
    row.incumbent_party = r.get("incumbent_party")
    dem = r.get("dem_candidate")
    rep = r.get("rep_candidate")
    row.dem_candidate_name = dem["name"] if isinstance(dem, dict) else dem
    row.rep_candidate_name = rep["name"] if isinstance(rep, dict) else rep
    row.source = "provider_ingest"
    session.flush()


def _as_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _write_history(session: Session, rows: list[HistoricalResultRow]) -> int:
    n = 0
    for r in rows:
        existing = session.execute(
            select(HistoricalElectionResult).where(
                HistoricalElectionResult.jurisdiction == r.jurisdiction,
                HistoricalElectionResult.year == r.year,
                HistoricalElectionResult.office == r.office,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = HistoricalElectionResult(
                jurisdiction=r.jurisdiction, year=r.year, office=r.office
            )
            session.add(existing)
            n += 1
        existing.dem_margin_pct = r.dem_margin_pct
        existing.dem_votes = r.dem_votes
        existing.rep_votes = r.rep_votes
        existing.provider = r.provider
        existing.source_url = r.source_url
        existing.retrieved_at = _utcnow()
    session.flush()
    return n


def _write_generic_ballot(session: Session, polls) -> int:
    n = 0
    for p in polls:
        ch = generic_ballot_content_hash(p)
        exists = session.execute(
            select(NationalEnvironmentObservation.id).where(
                NationalEnvironmentObservation.content_hash == ch
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        sponsor, internal = partisan_fields(p)
        session.add(
            NationalEnvironmentObservation(
                provider=p.provider,
                pollster=p.pollster,
                start_date=p.start_date,
                end_date=p.end_date,
                sample_size=p.sample_size,
                population=p.population,
                pollster_grade=p.pollster_grade,
                partisan_sponsor=sponsor,
                internal=internal,
                dem_pct=p.dem_pct,
                rep_pct=p.rep_pct,
                margin_dem=p.margin_dem,
                source_url=p.source_url,
                raw_payload_json=None,
                content_hash=ch,
                retrieved_at=_utcnow(),
            )
        )
        n += 1
    session.flush()
    return n


def _write_candidates(session: Session, records: list[CandidateRecord]) -> int:
    n = 0
    by_race: dict[str, list[CandidateRecord]] = {}
    for rec in records:
        by_race.setdefault(rec.race_id, []).append(rec)
    for race_id, recs in by_race.items():
        for rec in recs:
            existing = session.execute(
                select(RaceCandidate).where(
                    RaceCandidate.race_id == race_id,
                    RaceCandidate.normalized_name == rec.normalized_name,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = RaceCandidate(race_id=race_id, normalized_name=rec.normalized_name)
                session.add(existing)
                n += 1
            existing.name = rec.name
            existing.party = rec.party
            existing.is_incumbent = rec.is_incumbent
            existing.candidate_status = rec.candidate_status
            existing.fec_candidate_id = rec.fec_candidate_id
            existing.committee_ids_json = json.dumps(rec.committee_ids) if rec.committee_ids else None
            existing.provider = rec.provider
            existing.source_url = rec.source_url
            existing.retrieved_at = _utcnow()
        dem = next((r for r in recs if r.party == "DEM"), None)
        rep = next((r for r in recs if r.party == "REP"), None)
        inc_party = "DEM" if (dem and dem.is_incumbent) else "REP" if (rep and rep.is_incumbent) else None
        session.add(
            CandidateStatusSnapshot(
                race_id=race_id,
                observed_at=_utcnow(),
                dem_candidate=dem.name if dem else None,
                rep_candidate=rep.name if rep else None,
                incumbent_party=inc_party,
                open_seat=inc_party is None,
                nominees_confirmed=bool(dem and rep and dem.candidate_status in {"confirmed", "presumptive"}
                                       and rep.candidate_status in {"confirmed", "presumptive"}),
                mapping_confidence=1.0,
                provider=recs[0].provider if recs else "seed",
            )
        )
    session.flush()
    return n


def _write_polls(session: Session, rows) -> int:
    n = 0
    for r in rows:
        exists = session.execute(
            select(PollObservation.id).where(
                PollObservation.race_id == r.race_id,
                PollObservation.content_hash == r.content_hash,
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            PollObservation(
                race_id=r.race_id,
                provider=r.provider,
                provider_poll_id=r.provider_poll_id,
                pollster=r.pollster,
                start_date=r.start_date,
                end_date=r.end_date,
                sample_size=r.sample_size,
                population=r.population,
                pollster_grade=r.pollster_grade,
                partisan_sponsor=r.partisan_sponsor,
                internal=r.internal,
                dem_candidate=r.dem_candidate,
                rep_candidate=r.rep_candidate,
                dem_pct=r.dem_pct,
                rep_pct=r.rep_pct,
                margin_dem=r.margin_dem,
                source_url=r.source_url,
                normalized_payload_json=json.dumps(
                    {"match_confidence": r.match_confidence, "match_method": r.match_method}
                ),
                validation_status="OK",
                content_hash=r.content_hash,
                retrieved_at=_utcnow(),
            )
        )
        n += 1
    session.flush()
    return n


def ingest_political_data(
    session: Session,
    race_configs: list[dict],
    *,
    cycle: int = 2026,
    job_run_id: int | None = None,
    poll_chain=None,
    generic_ballot_chain=None,
    election_history_chain=None,
    candidate_chain_factory=None,
    lean_years: tuple[int, ...] = DEFAULT_LEAN_YEARS,
    allow_cache: bool = True,
) -> IngestSummary:
    summary = IngestSummary(cycle=cycle)
    known = _known_races(race_configs)
    race_config_by_id = {r["race_id"]: r for r in race_configs}

    for r in race_configs:
        _upsert_race(session, r)
        summary.races += 1

    # 1. election history (state lean inputs)
    eh_chain = election_history_chain or default_election_history_chain(years=lean_years)
    eh = eh_chain.run(session, target_ref="presidential_history", job_run_id=job_run_id, allow_cache=allow_cache)
    summary.chains["election_history"] = {
        "provider_used": eh.provider_used, "fallback_reason": eh.fallback_reason,
        "status": eh.result.status if eh.result else "FAILED",
    }
    if eh.result is not None and eh.result.usable:
        summary.history_rows = _write_history(session, eh.result.normalized_payload)

    # 2. national environment (generic ballot)
    gb_chain = generic_ballot_chain or default_generic_ballot_chain(cycle=cycle)
    gb = gb_chain.run(session, target_ref="generic_ballot", job_run_id=job_run_id, allow_cache=allow_cache)
    summary.chains["generic_ballot"] = {
        "provider_used": gb.provider_used, "fallback_reason": gb.fallback_reason,
        "status": gb.result.status if gb.result else "FAILED",
    }
    if gb.result is not None and gb.result.usable:
        summary.generic_ballot_rows = _write_generic_ballot(session, gb.result.normalized_payload)

    # 3. candidates (per race)
    cand_records: list[CandidateRecord] = []
    for r in race_configs:
        factory = candidate_chain_factory or (lambda rc=race_config_by_id: default_candidate_chain(race_config=rc))
        chain = factory()
        cr = chain.run(
            session, target_ref=r["race_id"], job_run_id=job_run_id, allow_cache=allow_cache,
            race_id=r["race_id"], state=r["state"], cycle=int(r["cycle"]), office=canonical_office(r["office"]),
        )
        summary.chains[f"candidate:{r['race_id']}"] = {
            "provider_used": cr.provider_used, "fallback_reason": cr.fallback_reason,
            "status": cr.result.status if cr.result else "FAILED",
        }
        if cr.result is not None and cr.result.usable:
            cand_records.extend(cr.result.normalized_payload)
    if cand_records:
        summary.candidate_rows = _write_candidates(session, cand_records)

    # 4. race polls
    p_chain = poll_chain or default_poll_chain(cycle=cycle)
    pr = p_chain.run(session, target_ref=f"polls:{cycle}", job_run_id=job_run_id, allow_cache=allow_cache)
    summary.chains["poll"] = {
        "provider_used": pr.provider_used, "fallback_reason": pr.fallback_reason,
        "status": pr.result.status if pr.result else "FAILED",
    }
    if pr.result is not None and pr.result.usable:
        obs_rows, skipped = polls_to_observations(pr.result.normalized_payload, known)
        summary.poll_rows = _write_polls(session, obs_rows)
        summary.poll_skipped = skipped

    return summary


# --------------------------------------------------------------------------------------------------
# providers -> DB -> engine bridge
# --------------------------------------------------------------------------------------------------
def build_quant_input_from_db(
    session: Session,
    race_id: str,
    *,
    as_of: datetime,
    lean_years: tuple[int, ...] = DEFAULT_LEAN_YEARS,
) -> Optional[QuantForecastInput]:
    """Assemble a market-free :class:`QuantForecastInput` from the ingested DB tables."""
    race = session.execute(select(Race).where(Race.race_id == race_id)).scalar_one_or_none()
    if race is None:
        return None

    polls = session.execute(
        select(PollObservation).where(PollObservation.race_id == race_id)
    ).scalars().all()
    norm_polls = tuple(
        NormalizedPoll(
            pollster=p.pollster,
            end_date=p.end_date,
            dem_pct=p.dem_pct,
            rep_pct=p.rep_pct,
            start_date=p.start_date,
            sample_size=p.sample_size,
            population=p.population,
            pollster_grade=p.pollster_grade,
            partisan_sponsor=p.partisan_sponsor,
            internal=bool(p.internal),
            poll_id=p.provider_poll_id,
            source=p.source_url,
        )
        for p in polls
        if p.end_date is not None
    )

    gb = session.execute(
        select(NationalEnvironmentObservation).order_by(NationalEnvironmentObservation.end_date.desc()).limit(60)
    ).scalars().all()
    norm_gb = tuple(
        GenericBallotPoll(
            pollster=g.pollster,
            end_date=g.end_date,
            dem_pct=g.dem_pct,
            rep_pct=g.rep_pct,
            sample_size=g.sample_size,
            population=g.population,
            pollster_grade=g.pollster_grade,
            partisan_sponsor=g.partisan_sponsor,
            internal=bool(g.internal),
        )
        for g in gb
        if g.end_date is not None
    )

    state = race.state.upper()[:2]
    hist_rows = session.execute(
        select(HistoricalElectionResult).where(
            HistoricalElectionResult.office == "president",
            HistoricalElectionResult.year.in_(lean_years),
            HistoricalElectionResult.jurisdiction.in_([state, "US"]),
        )
    ).scalars().all()
    state_by_year = {h.year: h for h in hist_rows if h.jurisdiction == state}
    nat_by_year = {h.year: h for h in hist_rows if h.jurisdiction == "US"}
    state_history = None
    if state_by_year and nat_by_year:
        common = sorted(set(state_by_year) & set(nat_by_year))
        if common:
            state_history = StateHistory(
                state=state,
                state_results={y: _pres_result(y, state_by_year[y]) for y in common},
                national_results={y: _pres_result(y, nat_by_year[y]) for y in common},
            )

    cands = session.execute(
        select(RaceCandidate).where(RaceCandidate.race_id == race_id)
    ).scalars().all()
    dem_c = next((c for c in cands if c.party == "DEM"), None)
    rep_c = next((c for c in cands if c.party == "REP"), None)
    dem_info = (
        CandidateInfo(dem_c.name, "DEM", is_incumbent=dem_c.is_incumbent, status=dem_c.candidate_status)
        if dem_c
        else (CandidateInfo(race.dem_candidate_name, "DEM") if race.dem_candidate_name else None)
    )
    rep_info = (
        CandidateInfo(rep_c.name, "REP", is_incumbent=rep_c.is_incumbent, status=rep_c.candidate_status)
        if rep_c
        else (CandidateInfo(race.rep_candidate_name, "REP") if race.rep_candidate_name else None)
    )

    latest_status = session.execute(
        select(CandidateStatusSnapshot)
        .where(CandidateStatusSnapshot.race_id == race_id)
        .order_by(CandidateStatusSnapshot.observed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    mapping_conf = (latest_status.mapping_confidence if latest_status and latest_status.mapping_confidence is not None else 1.0)
    if norm_polls:
        mps = [
            json.loads(p.normalized_payload_json).get("match_confidence", 1.0)
            for p in polls
            if p.normalized_payload_json
        ]
        if mps:
            mapping_conf = min(mapping_conf, min(mps))

    return QuantForecastInput(
        race=RaceMeta(
            race_id=race_id,
            state=state,
            office=canonical_office(race.office),  # type: ignore[arg-type]
            cycle=race.cycle,
            election_date=race.election_date,
            dem_candidate=dem_info,
            rep_candidate=rep_info,
        ),
        as_of=as_of,
        polls=norm_polls,
        generic_ballot=norm_gb,
        state_history=state_history,
        national_environment_stale=False,
        candidate_mapping_confidence=mapping_conf,
        provider_degraded=False,
    )


def _pres_result(year: int, row: HistoricalElectionResult) -> PresidentialResult:
    return PresidentialResult(
        year=year,
        dem_margin_pct=row.dem_margin_pct,
        dem_votes=row.dem_votes,
        rep_votes=row.rep_votes,
    )
