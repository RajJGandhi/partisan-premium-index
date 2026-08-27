"""The ingest orchestrator + the providers -> DB -> engine bridge (spec sections 41, 50 Phase E)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.providers.base import ProviderChain
from app.providers.ingest import build_quant_input_from_db, ingest_political_data
from app.providers.offline import (
    offline_candidate_chain_factory,
    offline_election_history_chain,
    offline_generic_ballot_chain,
    offline_poll_chain,
)
from app.quant.engine import run_quant_forecast

SEED_RACES = Path(__file__).resolve().parents[1] / "data" / "seed" / "quant_example_races.json"


def _configs():
    doc = json.loads(SEED_RACES.read_text())
    return [
        {
            "race_id": r["race_id"], "state": r["state"], "office": r["office"], "cycle": int(r["cycle"]),
            "election_date": r["election_date"], "dem_candidate": r.get("dem_candidate"),
            "rep_candidate": r.get("rep_candidate"), "incumbent_party": r.get("incumbent_party"),
        }
        for r in doc["races"]
    ]


def _offline_kwargs():
    return dict(
        poll_chain=offline_poll_chain(SEED_RACES),
        generic_ballot_chain=offline_generic_ballot_chain(SEED_RACES),
        election_history_chain=offline_election_history_chain(),
        candidate_chain_factory=offline_candidate_chain_factory(SEED_RACES),
    )


def test_ingest_writes_all_kinds(quant_db):
    from app.db.models_quant import (
        CandidateStatusSnapshot,
        HistoricalElectionResult,
        NationalEnvironmentObservation,
        PollObservation,
        ProviderHealth,
        Race,
        RaceCandidate,
    )

    with quant_db() as s:
        summary = ingest_political_data(s, _configs(), cycle=2026, **_offline_kwargs())
        s.commit()
        assert summary.races == 3
        assert summary.history_rows == 12  # 3 national + 9 illustrative state rows
        assert summary.generic_ballot_rows == 2
        assert summary.candidate_rows == 6
        assert summary.poll_rows == 7
        assert summary.poll_skipped == []
        assert s.query(Race).count() == 3
        assert s.query(PollObservation).count() == 7
        assert s.query(RaceCandidate).count() == 6
        assert s.query(CandidateStatusSnapshot).count() == 3
        assert s.query(NationalEnvironmentObservation).count() == 2
        assert s.query(HistoricalElectionResult).count() == 12
        assert s.query(ProviderHealth).count() >= 4
        assert all(h.status == "HEALTHY" for h in s.query(ProviderHealth))


def test_ingest_idempotent_same_db(quant_db):
    from app.db.models_quant import DataProviderRun, PollObservation

    session_factory = quant_db
    with session_factory() as s:
        s1 = ingest_political_data(s, _configs(), cycle=2026, **_offline_kwargs())
        s.commit()
    with session_factory() as s:
        s2 = ingest_political_data(s, _configs(), cycle=2026, **_offline_kwargs())
        s.commit()
        assert s2.poll_rows == 0 and s2.history_rows == 0 and s2.candidate_rows == 0
        assert s.query(PollObservation).count() == s1.poll_rows
        # every ingest still records its provider-run audit trail
        assert s.query(DataProviderRun).count() > 0


def test_generic_ballot_writer_tolerates_duplicate_rows_in_one_batch(quant_db):
    """A live feed (VoteHub) can repeat a release within one response; two rows that hash
    identically must not blow up the flush with a UNIQUE violation."""
    from datetime import date

    from app.db.models_quant import NationalEnvironmentObservation
    from app.providers.ingest import _write_generic_ballot
    from app.providers.national_environment import NormalizedGenericBallotPoll

    def _poll():
        return NormalizedGenericBallotPoll(
            pollster="YouGov", end_date=date(2026, 2, 23), start_date=date(2026, 2, 20),
            sample_size=1402, population="RV", pollster_grade=None, sponsor=None,
            dem_pct=45.0, rep_pct=41.0, source_url="https://example.com/x",
            provider="votehub_generic_ballot", provider_poll_id="1",
        )

    with quant_db() as s, s.no_autoflush:  # app's SessionLocal is autoflush=False
        n = _write_generic_ballot(s, [_poll(), _poll(), _poll()])  # identical -> one row
        s.commit()
        assert n == 1
        assert s.query(NationalEnvironmentObservation).count() == 1


def test_build_quant_input_from_db_matches_engine(quant_db):
    Session = quant_db
    as_of = datetime(2026, 8, 27, 13, tzinfo=timezone.utc)
    with Session() as s:
        ingest_political_data(s, _configs(), cycle=2026, **_offline_kwargs())
        s.commit()
    with Session() as s:
        inp = build_quant_input_from_db(s, "xx-sen-2026", as_of=as_of)
        assert inp is not None
        assert inp.race.state == "XX" and inp.race.office == "senate"
        assert len(inp.polls) == 4
        assert inp.state_history is not None
        assert len(inp.generic_ballot) == 2
        r1 = run_quant_forecast(inp)
        r2 = run_quant_forecast(inp)
        assert not r1.abstained
        assert r1.p_dem_win == r2.p_dem_win  # deterministic
        assert 0.0 < r1.p_dem_win < 1.0


def test_provider_failure_degrades_health_without_writing_zero(quant_db):
    from app.db.models_quant import NationalEnvironmentObservation, ProviderHealth
    from app.providers.base import ProviderError
    from app.providers.national_environment import GENERIC_BALLOT_KIND, DecisionDeskHqGenericBallotProvider

    class _Broken(DecisionDeskHqGenericBallotProvider):
        def enabled(self):
            return True

        def _do_fetch(self, **kwargs):
            raise ProviderError("ddhq generic ballot 503")

    broken_chain = ProviderChain(GENERIC_BALLOT_KIND, [_Broken(cycle=2026, backoff_base_seconds=0)])
    with quant_db() as s:
        summary = ingest_political_data(
            s, _configs(), cycle=2026,
            poll_chain=offline_poll_chain(SEED_RACES),
            generic_ballot_chain=broken_chain,
            election_history_chain=offline_election_history_chain(),
            candidate_chain_factory=offline_candidate_chain_factory(SEED_RACES),
        )
        s.commit()
        assert summary.generic_ballot_rows == 0
        assert s.query(NationalEnvironmentObservation).count() == 0  # missing, not zero rows
        assert summary.chains["generic_ballot"]["status"] == "FAILED"
        h = s.query(ProviderHealth).filter_by(provider_name="decisiondesk_generic_ballot").one()
        assert h.is_stale and h.status in {"DEGRADED", "DOWN"}
