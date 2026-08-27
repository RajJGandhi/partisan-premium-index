import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- PPI Quant test helpers ---------------------------------------------------------------------
@pytest.fixture
def make_poll():
    """Factory for app.quant.types.NormalizedPoll with sensible defaults."""
    from app.quant.types import NormalizedPoll

    def _make(
        *,
        pollster="Test Pollster",
        end_date=date(2026, 8, 20),
        dem_pct=50.0,
        rep_pct=45.0,
        sample_size=800,
        population="LV",
        pollster_grade="B",
        partisan_sponsor=None,
        internal=False,
        start_date=None,
        poll_id=None,
    ):
        return NormalizedPoll(
            pollster=pollster,
            end_date=end_date,
            dem_pct=dem_pct,
            rep_pct=rep_pct,
            sample_size=sample_size,
            population=population,
            pollster_grade=pollster_grade,
            partisan_sponsor=partisan_sponsor,
            internal=internal,
            start_date=start_date,
            poll_id=poll_id,
        )

    return _make


@pytest.fixture
def make_input():
    """Factory for a complete app.quant.types.QuantForecastInput (statewide race)."""
    from app.quant.types import (
        CandidateInfo,
        PresidentialResult,
        QuantForecastInput,
        RaceMeta,
        StateHistory,
    )

    def _make(
        *,
        race_id="tx-sen-2026",
        state="TX",
        office="senate",
        election_date=date(2026, 11, 3),
        as_of=datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc),
        polls=(),
        generic_ballot=(),
        dem_incumbent=False,
        rep_incumbent=False,
        dem_status="confirmed",
        rep_status="confirmed",
        include_candidates=True,
        state_margins=(("2016", 1.0), ("2020", 2.0), ("2024", -1.0)),
        national_margins=(("2016", 2.1), ("2020", 4.5), ("2024", -1.5)),
        include_history=True,
        national_environment_override=None,
        national_environment_stale=False,
        candidate_mapping_confidence=1.0,
        provider_degraded=False,
    ):
        dem = CandidateInfo("D. Candidate", "DEM", is_incumbent=dem_incumbent, status=dem_status)
        rep = CandidateInfo("R. Candidate", "REP", is_incumbent=rep_incumbent, status=rep_status)
        history = None
        if include_history:
            history = StateHistory(
                state=state,
                state_results={
                    int(y): PresidentialResult(int(y), dem_margin_pct=m) for y, m in state_margins
                },
                national_results={
                    int(y): PresidentialResult(int(y), dem_margin_pct=m) for y, m in national_margins
                },
            )
        return QuantForecastInput(
            race=RaceMeta(
                race_id=race_id,
                state=state,
                office=office,
                cycle=2026,
                election_date=election_date,
                dem_candidate=dem if include_candidates else None,
                rep_candidate=rep if include_candidates else None,
            ),
            as_of=as_of,
            polls=tuple(polls),
            generic_ballot=tuple(generic_ballot),
            state_history=history,
            national_environment_override=national_environment_override,
            national_environment_stale=national_environment_stale,
            candidate_mapping_confidence=candidate_mapping_confidence,
            provider_degraded=provider_degraded,
        )

    return _make


@pytest.fixture
def blind_bundle(make_input, make_poll):
    """A built EvidenceBundle for a favourable D race (for blind-forecast tests)."""
    from app.quant.engine import run_quant_forecast
    from app.quant.evidence_bundle import build_quant_evidence_bundle

    inp = make_input(
        polls=[
            make_poll(pollster="A", dem_pct=52, rep_pct=45),
            make_poll(pollster="B", dem_pct=51, rep_pct=46, end_date=date(2026, 8, 15)),
        ],
        dem_incumbent=True,
    )
    return build_quant_evidence_bundle(inp, run_quant_forecast(inp))


@pytest.fixture
def quant_db(tmp_path):
    """A fresh SQLite session factory with the full schema (incl. the Quant v1.5 tables)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import models  # noqa: F401  (registers models_quant on Base.metadata)
    from app.db.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'quant.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)
