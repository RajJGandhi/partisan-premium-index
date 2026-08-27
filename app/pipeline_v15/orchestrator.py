"""The 10-stage PPI v1.5 twice-daily pipeline (spec section 41).

``run_v15_pipeline`` runs discover -> market snapshot -> political data -> validate -> quant ->
evidence bundle -> blind -> ensemble -> comparison -> publish. It records one ``JobRun``; each race's
stages 5-9 run inside a SAVEPOINT so one race failing cannot roll back the others. No human
approval gate. The headline series is not changed here (see ``app.pipeline_v15.cutover``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blind.ensemble_runner import compute_and_persist_ensemble
from app.blind.providers import (
    AnthropicBlindProvider,
    BlindForecastProvider,
    DeterministicBlindProvider,
    OpenAIBlindProvider,
)
from app.blind.runner import run_blind_forecasts
from app.db.models import JobRun
from app.db.models_quant import PollObservation, Race
from app.pipeline_v15.comparison import join_forecasts_with_market
from app.pipeline_v15.cutover import headline_series
from app.pipeline_v15.market_discovery import discover_and_bind
from app.pipeline_v15.persist import persist_quant_forecast
from app.providers.ingest import build_quant_input_from_db, ingest_political_data
from app.quant.adapters import StatewideRaceAdapter
from app.quant.config import ENSEMBLE_METHODOLOGY_VERSION, QUANT_V1, MethodologyConfig
from app.quant.evidence_bundle import build_quant_evidence_bundle


@dataclass
class RaceOutcome:
    race_id: str
    status: str  # OK / ABSTAIN / ERROR
    data_quality: Optional[str] = None
    p_dem_win: Optional[float] = None
    ensemble_probability: Optional[float] = None
    ensemble_available: bool = False
    blind_statuses: dict[str, str] = field(default_factory=dict)
    market_model_spread: Optional[float] = None
    robustness: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PipelineSummary:
    run_key: str
    job_run_id: Optional[int]
    headline_series: str
    stages: dict[str, Any] = field(default_factory=dict)
    races: list[RaceOutcome] = field(default_factory=list)
    status: str = "OK"

    def as_dict(self) -> dict:
        return {
            "run_key": self.run_key,
            "job_run_id": self.job_run_id,
            "headline_series": self.headline_series,
            "status": self.status,
            "stages": self.stages,
            "races": [r.__dict__ for r in self.races],
        }


def _blind_providers(mode: str | None, bundle) -> list[BlindForecastProvider]:
    if mode == "stub":
        return [
            DeterministicBlindProvider(bundle=bundle, standing_in_for="openai"),
            DeterministicBlindProvider(bundle=bundle, standing_in_for="anthropic", spread_pts=4.5),
        ]
    if mode == "live":
        return [OpenAIBlindProvider(), AnthropicBlindProvider()]
    return []


def _default_run_key(as_of: datetime) -> str:
    slot = "primary" if as_of.hour < 17 else "backup"
    return f"ppi-v15:{as_of.date().isoformat()}:{slot}"


def _canonical_as_of(run_key: str) -> datetime | None:
    """Derive a deterministic ``as_of`` from a ``ppi-v15:<date>:<slot>`` run_key so every stage of
    a given slot uses the same evidence cutoff and the whole pipeline is idempotent per run_key."""
    parts = run_key.split(":")
    if len(parts) < 3:
        return None
    try:
        d = date.fromisoformat(parts[1])
    except ValueError:
        return None
    hour = 13 if parts[2].startswith("primary") else 1
    return datetime.combine(d, time(hour, 0), tzinfo=timezone.utc)


def run_v15_pipeline(
    session: Session,
    *,
    race_configs: Sequence[dict],
    cycle: int = 2026,
    run_key: str | None = None,
    as_of: datetime | None = None,
    blind_mode: str | None = None,  # None / "stub" / "live"
    discovery_provider=None,
    market_client=None,  # a TrackedPolymarketClient-like object; None -> stage 2 skipped
    ingest_kwargs: dict | None = None,  # e.g. offline chains for tests
    cfg: MethodologyConfig = QUANT_V1,
    trigger_type: str = "manual",
) -> PipelineSummary:
    now = datetime.now(timezone.utc)
    rk = run_key or _default_run_key(as_of or now)
    # a fixed cutoff per run_key -> the whole pipeline is idempotent for a given slot
    as_of = as_of or _canonical_as_of(rk) or now
    summary = PipelineSummary(run_key=rk, job_run_id=None, headline_series=headline_series())

    job = JobRun(run_key=rk, job_name="ppi-v15-daily", trigger_type=trigger_type,
                 started_at=as_of, status="RUNNING", pipeline_mode="strict_llm_only")
    existing_job = session.execute(select(JobRun).where(JobRun.run_key == rk)).scalar_one_or_none()
    if existing_job is not None:
        job = existing_job
        job.status = "RUNNING"
    else:
        session.add(job)
    session.flush()
    summary.job_run_id = job.id

    # --- stage 1: discover -----------------------------------------------------------------------
    if discovery_provider is not None:
        try:
            disc = discover_and_bind(session, discovery_provider=discovery_provider, job_run_id=job.id)
            summary.stages["1_discover"] = disc.as_dict()
        except Exception as exc:  # discovery failure must not abort the run
            summary.stages["1_discover"] = {"error": f"{type(exc).__name__}: {exc}"}
    else:
        summary.stages["1_discover"] = {"skipped": "no discovery provider supplied"}

    # --- stage 2: market snapshot -------------------------------------------------------------
    if market_client is not None:
        summary.stages["2_market_snapshot"] = _snapshot_markets(session, market_client)
    else:
        summary.stages["2_market_snapshot"] = {"skipped": "no market client supplied (build-to-seam)"}

    # --- stage 3 + 4: political data + validate --------------------------------------------
    try:
        ing = ingest_political_data(session, list(race_configs), cycle=cycle, job_run_id=job.id,
                                    **(ingest_kwargs or {}))
        summary.stages["3_political_data"] = {
            "history_rows": ing.history_rows, "generic_ballot_rows": ing.generic_ballot_rows,
            "candidate_rows": ing.candidate_rows, "poll_rows": ing.poll_rows,
            "chains": {k: v.get("provider_used") for k, v in ing.chains.items()},
        }
        summary.stages["4_validate"] = _validate_inputs(session, [rc["race_id"] for rc in race_configs],
                                                        skipped=ing.poll_skipped)
    except Exception as exc:
        summary.stages["3_political_data"] = {"error": f"{type(exc).__name__}: {exc}"}
        job.status = "FAILED"
        job.sanitized_error = str(exc)[:2000]
        session.flush()
        summary.status = "FAILED"
        return summary

    # --- stages 5-9 per race (SAVEPOINT-isolated) ------------------------------------------
    adapter = StatewideRaceAdapter()
    n_ok = n_abstain = n_error = 0
    for rc in race_configs:
        race_id = rc["race_id"]
        outcome = RaceOutcome(race_id=race_id, status="OK")
        try:
            with session.begin_nested():  # SAVEPOINT: an error here rolls back only this race
                inp = build_quant_input_from_db(session, race_id, as_of=as_of)
                if inp is None:
                    outcome.status = "ERROR"
                    outcome.error = "no ingested data for race"
                    raise _SkipRace()

                result = adapter.forecast(inp, cfg).quant_result
                assert result is not None
                bundle = build_quant_evidence_bundle(inp, result)
                qf_row, _created = persist_quant_forecast(
                    session, inp, result, bundle, run_key=rk, job_run_id=job.id,
                    race_source=rc.get("source", "market_discovery"),
                )
                outcome.data_quality = result.data_quality
                outcome.p_dem_win = result.p_dem_win
                outcome.status = "ABSTAIN" if result.abstained else "OK"

                # stage 6 is the bundle (persisted inside persist_quant_forecast); stage 7-8:
                blind_rows = []
                if blind_mode:
                    contract_q = (
                        f"Will the Democratic candidate win the {inp.race.state} {inp.race.office} "
                        f"general election in {inp.race.cycle}?"
                    )
                    bsum = run_blind_forecasts(
                        session, race_id=race_id, run_key=rk, evidence_bundle=bundle,
                        contract_question=contract_q, providers=_blind_providers(blind_mode, bundle),
                        run_slot=rk.split(":")[-1], job_run_id=job.id,
                    )
                    blind_rows = bsum.rows
                    outcome.blind_statuses = {r.provider: r.status for r in blind_rows}

                ens_row, _ = compute_and_persist_ensemble(
                    session, race_id=race_id, run_key=rk, quant_forecast=qf_row,
                    blind_rows=blind_rows, methodology_version=ENSEMBLE_METHODOLOGY_VERSION,
                    job_run_id=job.id,
                )
                outcome.ensemble_available = ens_row.available
                outcome.ensemble_probability = ens_row.ensemble_probability

                # stage 9: comparison (no-op unless the race has a market snapshot + yes-party)
                for cmp in join_forecasts_with_market(session, race_id=race_id, run_key=rk, cfg=cfg):
                    if cmp.series == "ensemble":
                        outcome.market_model_spread = cmp.market_model_spread
                        outcome.robustness = cmp.robustness
        except _SkipRace:
            pass
        except Exception as exc:  # noqa: BLE001 -- recorded per race, run continues
            outcome.status = "ERROR"
            outcome.error = f"{type(exc).__name__}: {exc}"

        summary.races.append(outcome)
        n_ok += outcome.status == "OK"
        n_abstain += outcome.status == "ABSTAIN"
        n_error += outcome.status == "ERROR"

    # --- stage 10: publish -----------------------------------------------------------------
    summary.stages["5_9_forecasts"] = {"ok": n_ok, "abstained": n_abstain, "errors": n_error}
    summary.stages["10_publish"] = {
        "headline_series": summary.headline_series,
        "note": "v1.5 rows are append-only and immutable; headline flip is manual "
        "(docs/research/PPI_CUTOVER.md)" if summary.headline_series == "legacy_blind_llm"
        else f"headline is '{summary.headline_series}'",
    }

    job.finished_at = datetime.now(timezone.utc)
    job.status = "FAILED" if n_error and not n_ok else ("PARTIAL" if n_error else "OK")
    job.markets_attempted = len(race_configs)
    job.markets_succeeded = n_ok
    session.flush()
    summary.status = job.status
    return summary


class _SkipRace(Exception):
    pass


def _snapshot_markets(session: Session, client) -> dict:
    """Snapshot every linked Polymarket contract via ``client.snapshot_market(session, market)``."""
    from app.db.models import Market

    linked = session.execute(
        select(Market).join(Race, Race.polymarket_market_id == Market.id)
    ).scalars().all()
    ok = err = 0
    for m in linked:
        try:
            client.snapshot_market(session, m)  # duck-typed; real client wires app/ppi/polymarket
            ok += 1
        except Exception:  # noqa: BLE001
            err += 1
    return {"linked_markets": len(linked), "snapshots_written": ok, "errors": err}


def _validate_inputs(session: Session, race_ids: list[str], *, skipped: list[dict]) -> dict:
    invalid = session.execute(
        select(PollObservation).where(
            PollObservation.race_id.in_(race_ids), PollObservation.validation_status != "OK"
        )
    ).scalars().all()
    return {
        "races": len(race_ids),
        "invalid_poll_observations": len(invalid),
        "poll_ingest_skipped": len(skipped),
        "skip_reasons": sorted({s.get("reason") for s in skipped}),
    }


def default_run_key(as_of: datetime | None = None) -> str:
    return _default_run_key(as_of or datetime.now(timezone.utc))
