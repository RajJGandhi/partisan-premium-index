"""Run PPI Quant v1.0 in **shadow mode** (spec section 50, Phase D).

Reads structured race inputs (a JSON file -- ``data/seed/quant_example_races.json`` by default, or
whatever the political-data provider layer will materialise later), runs the deterministic Quant
engine for each race, and appends:

  * a ``races`` row (idempotent upsert of identity)
  * an immutable ``quant_evidence_bundles`` row (timestamp-locked, market-free)
  * an append-only ``quant_forecasts`` row per (race_id, run_key, methodology_version)
  * a write-once ``methodology_versions`` row for the config that produced them
  * an ``ensemble_forecasts`` row -- marked ``available = False`` here, because the GPT/Claude
    blind benchmarks are not wired yet, and the ensemble is NEVER silently reweighted to the
    components that are present (spec section 25)

It does **not** touch ``llm_forecasts`` (the legacy blind-LLM series), ``market_snapshots``,
``daily_index``, ``blind_index_runs``, the public export bundle, or any headline series. It never
reads a market price. Re-running the same slot is a no-op.

Usage::

    PYTHONPATH=. python scripts/run_quant_shadow.py --dry-run
    PYTHONPATH=. python scripts/run_quant_shadow.py
    PYTHONPATH=. python scripts/run_quant_shadow.py --races path/to/races.json --run-key quant-shadow:2026-08-27:primary
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.blind.ensemble_runner import compute_and_persist_ensemble
from app.blind.providers import (
    AnthropicBlindProvider,
    BlindForecastProvider,
    DeterministicBlindProvider,
    OpenAIBlindProvider,
)
from app.blind.runner import run_blind_forecasts
from app.db.database import get_session
from app.db.models_quant import (
    QuantEvidenceBundle,
    QuantForecast,
    Race,
)
from app.quant.adapters import StatewideRaceAdapter
from app.quant.append_only import (
    record_methodology_version,
    upsert_quant_forecast,
)
from app.quant.config import ENSEMBLE_METHODOLOGY_VERSION, QUANT_V1
from app.quant.evidence_bundle import build_quant_evidence_bundle
from app.quant.types import (
    CandidateInfo,
    GenericBallotPoll,
    NormalizedPoll,
    PresidentialResult,
    QuantForecastInput,
    RaceMeta,
    StateHistory,
)

DEFAULT_RACES = Path("data/seed/quant_example_races.json")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _parse_dt(value: str) -> datetime:
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _candidate(raw: dict | None) -> CandidateInfo | None:
    if not raw:
        return None
    return CandidateInfo(
        name=raw["name"],
        party=raw["party"],
        is_incumbent=bool(raw.get("is_incumbent", False)),
        status=raw.get("status", "confirmed"),
        source=raw.get("source"),
    )


def _state_history(state: str, raw: dict | None) -> StateHistory | None:
    if not raw:
        return None
    sr = {int(y): PresidentialResult(int(y), dem_margin_pct=float(m)) for y, m in raw.get("state_results", {}).items()}
    nr = {int(y): PresidentialResult(int(y), dem_margin_pct=float(m)) for y, m in raw.get("national_results", {}).items()}
    if not sr or not nr:
        return None
    return StateHistory(state=state, state_results=sr, national_results=nr)


def _build_input(race_raw: dict, as_of: datetime) -> QuantForecastInput:
    state = race_raw["state"].upper()
    polls = tuple(
        NormalizedPoll(
            pollster=p["pollster"],
            end_date=_parse_date(p["end_date"]),  # type: ignore[arg-type]
            dem_pct=float(p["dem_pct"]),
            rep_pct=float(p["rep_pct"]),
            start_date=_parse_date(p.get("start_date")),
            sample_size=p.get("sample_size"),
            population=p.get("population"),
            pollster_grade=p.get("pollster_grade"),
            partisan_sponsor=p.get("partisan_sponsor"),
            internal=bool(p.get("internal", False)),
            poll_id=p.get("poll_id"),
            source=p.get("source"),
        )
        for p in race_raw.get("polls", [])
    )
    generic = tuple(
        GenericBallotPoll(
            pollster=g["pollster"],
            end_date=_parse_date(g["end_date"]),  # type: ignore[arg-type]
            dem_pct=float(g["dem_pct"]),
            rep_pct=float(g["rep_pct"]),
            sample_size=g.get("sample_size"),
            population=g.get("population"),
            pollster_grade=g.get("pollster_grade"),
        )
        for g in race_raw.get("generic_ballot", [])
    )
    race = RaceMeta(
        race_id=race_raw["race_id"],
        state=state,
        office=race_raw["office"],
        cycle=int(race_raw["cycle"]),
        election_date=_parse_date(race_raw["election_date"]),  # type: ignore[arg-type]
        dem_candidate=_candidate(race_raw.get("dem_candidate")),
        rep_candidate=_candidate(race_raw.get("rep_candidate")),
    )
    return QuantForecastInput(
        race=race,
        as_of=as_of,
        polls=polls,
        generic_ballot=generic,
        state_history=_state_history(state, race_raw.get("state_history")),
        national_environment_override=race_raw.get("national_environment_override"),
        national_environment_stale=bool(race_raw.get("national_environment_stale", False)),
        candidate_mapping_confidence=float(race_raw.get("candidate_mapping_confidence", 1.0)),
        provider_degraded=bool(race_raw.get("provider_degraded", False)),
        notes=tuple(race_raw.get("notes", [])),
    )


def _default_run_key(as_of: datetime) -> str:
    slot = "primary" if as_of.hour < 17 else "backup"
    return f"quant-shadow:{as_of.date().isoformat()}:{slot}"


def run(
    races_path: Path,
    run_key: str | None,
    dry_run: bool,
    from_db: bool = False,
    blind_mode: str | None = None,
) -> dict[str, Any]:
    doc = json.loads(races_path.read_text()) if races_path.exists() else {"races": []}
    as_of = _parse_dt(doc.get("as_of") or datetime.now(timezone.utc).isoformat())
    rk = run_key or _default_run_key(as_of)
    cfg = QUANT_V1
    adapter = StatewideRaceAdapter()

    summary: dict[str, Any] = {
        "run_key": rk,
        "as_of": as_of.isoformat(),
        "methodology_version": cfg.version,
        "config_hash": cfg.config_hash(),
        "source": "db" if from_db else "seed_file",
        "blind_mode": blind_mode,
        "races": [],
        "written": 0,
        "skipped_existing": 0,
        "abstained": 0,
        "blind_tokens": 0,
        "dry_run": dry_run,
    }

    with get_session() as session:
        if not dry_run:
            record_methodology_version(
                session,
                version=cfg.version,
                kind="quant",
                config=cfg.as_dict(),
                config_hash=cfg.config_hash(),
                provisional=True,
                notes="PPI Quant v1.0 -- provisional constants, see docs/research/PPI_QUANT_V1.md",
            )

        if from_db:
            from app.providers.ingest import build_quant_input_from_db

            race_ids = [r.race_id for r in session.query(Race).order_by(Race.race_id).all()]
            inputs = [
                inp for rid in race_ids
                if (inp := build_quant_input_from_db(session, rid, as_of=as_of)) is not None
            ]
        else:
            inputs = [_build_input(race_raw, as_of) for race_raw in doc["races"]]

        for inp in inputs:
            outcome = adapter.forecast(inp, cfg)
            result = outcome.quant_result
            assert result is not None
            bundle = build_quant_evidence_bundle(inp, result)

            line = {
                "race_id": result.race_id,
                "adapter_status": outcome.status,
                "data_quality": result.data_quality,
                "abstained": result.abstained,
                "polling_margin": None if result.polling_margin is None else round(result.polling_margin, 3),
                "fundamental_margin": None
                if result.fundamental_margin is None
                else round(result.fundamental_margin, 3),
                "poll_weight": round(result.poll_weight, 3),
                "expected_margin": None if result.expected_margin is None else round(result.expected_margin, 3),
                "sigma_total": None if result.uncertainty is None else round(result.uncertainty.sigma_total, 3),
                "p_dem_win": None if result.p_dem_win is None else round(result.p_dem_win, 4),
                "evidence_bundle_hash": bundle.content_hash[:12],
                "input_hash": result.input_hash[:12],
            }
            summary["races"].append(line)
            if result.abstained:
                summary["abstained"] += 1

            if dry_run:
                continue

            race_row = (
                session.query(Race).filter(Race.race_id == result.race_id).one_or_none()
            )
            if race_row is None:
                race_row = Race(race_id=result.race_id)
                session.add(race_row)
            race_row.state = inp.race.state
            race_row.office = inp.race.office
            race_row.cycle = inp.race.cycle
            race_row.election_date = inp.race.election_date
            race_row.adapter_type = "statewide_race"
            race_row.dem_candidate_name = inp.race.dem_candidate.name if inp.race.dem_candidate else None
            race_row.rep_candidate_name = inp.race.rep_candidate.name if inp.race.rep_candidate else None
            race_row.incumbent_party = inp.race.incumbent_party
            race_row.source = "seed:quant_example_races"
            session.flush()

            eb = QuantEvidenceBundle(
                race_id=result.race_id,
                run_key=rk,
                forecast_timestamp=as_of,
                election_date=inp.race.election_date,
                evidence_cutoff=as_of,
                payload_json=json.dumps(bundle.payload, sort_keys=True, separators=(",", ":"), default=str),
                content_hash=bundle.content_hash,
                source_manifest_json=json.dumps(bundle.payload.get("source_manifest", [])),
            )
            existing_eb = (
                session.query(QuantEvidenceBundle)
                .filter(QuantEvidenceBundle.content_hash == bundle.content_hash)
                .one_or_none()
            )
            if existing_eb is None:
                session.add(eb)
                session.flush()
                bundle_id = eb.id
            else:
                bundle_id = existing_eb.id

            u = result.uncertainty
            qf = QuantForecast(
                race_id=result.race_id,
                run_key=rk,
                run_slot=rk.split(":")[-1],
                adapter_type="statewide_race",
                methodology_version=result.methodology_version,
                config_hash=result.config_hash,
                evidence_bundle_id=bundle_id,
                evidence_bundle_hash=bundle.content_hash,
                input_hash=result.input_hash,
                generated_at=result.generated_at,
                evidence_cutoff=as_of,
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
                fair_value_yes=None,  # bound to a specific contract only when a market is mapped
                n_eff=None if result.polling is None else result.polling.n_eff,
                used_poll_count=None if result.polling is None else result.polling.used_poll_count,
                latest_poll_date=None if result.polling is None else result.polling.latest_poll_date,
                state_lean=None if result.fundamentals is None else result.fundamentals.state_lean,
                national_environment=None
                if result.fundamentals is None
                else result.fundamentals.national_environment,
                incumbency_points=None
                if result.fundamentals is None
                else result.fundamentals.incumbency_adjustment,
                detail_json=json.dumps(result.detail, default=str),
                pipeline_mode="shadow",
                publication_status="SHADOW",
            )
            _row, created = upsert_quant_forecast(session, qf)
            if created:
                summary["written"] += 1
            else:
                summary["skipped_existing"] += 1

            # --- blind benchmarks (spec 23/24) + ensemble (spec 25) ---------------------------
            blind_rows: list = []
            if blind_mode:
                blind_providers: list[BlindForecastProvider]
                if blind_mode == "stub":
                    blind_providers = [
                        DeterministicBlindProvider(bundle=bundle, standing_in_for="openai"),
                        DeterministicBlindProvider(bundle=bundle, standing_in_for="anthropic", spread_pts=4.5),
                    ]
                else:
                    blind_providers = [OpenAIBlindProvider(), AnthropicBlindProvider()]
                contract_q = (
                    f"Will the Democratic candidate win the {inp.race.state} {inp.race.office} "
                    f"general election in {inp.race.cycle}?"
                )
                bsum = run_blind_forecasts(
                    session,
                    race_id=result.race_id,
                    run_key=rk,
                    evidence_bundle=bundle,
                    evidence_bundle_id=bundle_id,
                    contract_question=contract_q,
                    providers=blind_providers,
                    run_slot=rk.split(":")[-1],
                )
                blind_rows = bsum.rows
                line["blind"] = [
                    {"provider": r.provider, "status": r.status, "probability": r.probability}
                    for r in blind_rows
                ]
                summary["blind_tokens"] += bsum.total_tokens

            ens_row, _ens_created = compute_and_persist_ensemble(
                session,
                race_id=result.race_id,
                run_key=rk,
                quant_forecast=_row,
                blind_rows=blind_rows,
                methodology_version=ENSEMBLE_METHODOLOGY_VERSION,
            )
            line["ensemble"] = {
                "available": ens_row.available,
                "probability": None if ens_row.ensemble_probability is None else round(ens_row.ensemble_probability, 4),
                "robustness": ens_row.robustness,
            }

    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run PPI Quant v1.0 in shadow mode.")
    ap.add_argument("--races", type=Path, default=DEFAULT_RACES, help="race-input JSON file")
    ap.add_argument("--run-key", default=None, help="override the run_key (default quant-shadow:<date>:<slot>)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    ap.add_argument(
        "--from-db",
        action="store_true",
        help="build inputs from the ingested DB tables (poll_observations, historical_election_results, "
        "race_candidates, ...) instead of the seed JSON -- the providers -> DB -> engine path",
    )
    ap.add_argument(
        "--blind",
        action="store_true",
        help="also run the GPT + Claude blind benchmarks (SKIPPED_PROVIDER rows without API keys) "
        "and compute the real ensemble",
    )
    ap.add_argument(
        "--blind-stub",
        action="store_true",
        help="run the deterministic blind stub (offline plumbing test); rows are flagged STUB",
    )
    args = ap.parse_args()

    blind_mode = "stub" if args.blind_stub else ("live" if args.blind else None)
    summary = run(args.races, args.run_key, args.dry_run, from_db=args.from_db, blind_mode=blind_mode)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
