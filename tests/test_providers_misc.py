"""National environment, election history, candidates, and market classification providers."""

from __future__ import annotations

from pathlib import Path

from app.providers.candidates import OpenFecCandidateProvider, _party
from app.providers.election_history import SeedCsvElectionHistoryProvider
from app.providers.markets import (
    AMBIGUOUS,
    SUPPORTED_HOUSE_CONTROL,
    SUPPORTED_SENATE_CONTROL,
    SUPPORTED_STATEWIDE_RACE,
    UNSUPPORTED,
    classify_market,
)
from app.providers.national_environment import DecisionDeskHqGenericBallotProvider

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


# --- generic ballot ----------------------------------------------------------------------------
class _FakeDDHQGeneric(DecisionDeskHqGenericBallotProvider):
    ROWS = [
        {"poll_id": 5, "pollster": "YouGov", "start_date": "2026-08-10", "end_date": "2026-08-13",
         "sample_size": 1500, "population": "rv", "cycle": "2026", "office_type": "House",
         "dem_pct": 47, "rep_pct": 44, "other_pct": 9},
        {"poll_id": 6, "pollster": "Marist", "start_date": "2026-08-05", "end_date": "2026-08-08",
         "sample_size": 1200, "population": "lv", "cycle": "2024", "dem_pct": 50, "rep_pct": 48},  # wrong cycle
    ]

    def _do_fetch(self, **kwargs):
        return self.ROWS, "https://polling.decisiondeskhq.com/api/v1/polls/generic_ballot", 200


def test_generic_ballot_normalizer_filters_cycle_and_computes_margin(quant_db):
    with quant_db() as s:
        res = _FakeDDHQGeneric(cycle=2026, backoff_base_seconds=0).fetch(s)
    polls = res.normalized_payload
    assert len(polls) == 1
    assert polls[0].dem_pct == 47 and polls[0].rep_pct == 44
    assert polls[0].margin_dem == 3
    assert polls[0].population == "RV"


# --- election history ------------------------------------------------------------------------
def test_seed_csv_election_history_reads_national_and_state(quant_db):
    with quant_db() as s:
        res = SeedCsvElectionHistoryProvider(backoff_base_seconds=0).fetch(s)
    rows = res.normalized_payload
    us = [r for r in rows if r.jurisdiction == "US"]
    assert {r.year for r in us} == {2016, 2020, 2024}
    assert any(r.jurisdiction == "XX" for r in rows)  # illustrative placeholder rows present
    us2024 = next(r for r in us if r.year == 2024)
    assert us2024.dem_margin_pct == -1.5  # Trump +1.5


def test_seed_csv_election_history_disabled_without_files(tmp_path, quant_db):
    with quant_db() as s:
        p = SeedCsvElectionHistoryProvider(
            national_csv=tmp_path / "nope.csv", state_csv=tmp_path / "nope2.csv", backoff_base_seconds=0
        )
        res = p.fetch(s)
    assert res.status == "EMPTY"


# --- candidates -------------------------------------------------------------------------------
class _FakeOpenFEC(OpenFecCandidateProvider):
    def enabled(self):
        return True

    def _do_fetch(self, *, race_id, state, cycle, office="senate", **kwargs):
        return (
            {
                "race_id": race_id,
                "results": [
                    {"name": "SMITH, JANE", "party": "DEM", "incumbent_challenge": "C",
                     "candidate_id": "S1", "candidate_status": "C", "has_raised_funds": True},
                    {"name": "JONES, JOHN", "party": "REP", "incumbent_challenge": "I",
                     "candidate_id": "S2", "candidate_status": "C", "has_raised_funds": True},
                    {"name": "NADER, RALPH", "party": "IND", "candidate_id": "S3"},
                ],
            },
            "https://api.open.fec.gov/v1/candidates/search/",
            200,
        )


def test_openfec_candidate_mapping(quant_db):
    with quant_db() as s:
        res = _FakeOpenFEC(backoff_base_seconds=0).fetch(
            s, race_id="nc-sen-2026", state="NC", cycle=2026, office="senate"
        )
    recs = {r.party: r for r in res.normalized_payload}
    assert set(recs) == {"DEM", "REP"}  # independent dropped
    assert recs["REP"].is_incumbent is True and recs["DEM"].is_incumbent is False
    assert recs["DEM"].fec_candidate_id == "S1"


def test_party_helper():
    assert _party("Democratic") == "DEM"
    assert _party("GOP") == "REP"
    assert _party("Libertarian") == "OTHER"


# --- market classification ------------------------------------------------------------------
def test_classify_senate_control():
    c = classify_market("Will the Republican Party control the Senate after the 2026 Midterm elections?")
    assert c.category == SUPPORTED_SENATE_CONTROL and c.confidence >= 0.9


def test_classify_house_control_supported_label_but_adapter_unavailable():
    c = classify_market("Which party will control the House of Representatives after 2026?")
    assert c.category == SUPPORTED_HOUSE_CONTROL


def test_classify_statewide_race_with_state_and_year():
    c = classify_market("Will Democrats win the North Carolina U.S. Senate race in 2026?")
    assert c.category == SUPPORTED_STATEWIDE_RACE
    assert c.race_hint == {
        "state": "NC", "office": "senate", "cycle": 2026, "race_id": "nc-sen-2026", "yes_party": "DEM",
    }


def test_classify_governor_race():
    c = classify_market("Will the Republican win the 2026 Michigan gubernatorial election?")
    assert c.category == SUPPORTED_STATEWIDE_RACE
    assert c.race_hint["office"] == "governor" and c.race_hint["state"] == "MI"
    assert c.race_hint["yes_party"] == "REP"


def test_classify_statewide_race_without_a_named_party():
    c = classify_market("Which party will win the 2026 Georgia U.S. Senate race?")
    assert c.category == SUPPORTED_STATEWIDE_RACE
    assert c.race_hint["yes_party"] is None  # unset -> market series excluded from that race's scoring


def test_classify_unsupported_and_ambiguous():
    assert classify_market("Will Donald Trump pardon himself in 2026?").category == UNSUPPORTED
    assert classify_market("Will there be a government shutdown before October 2026?").category == UNSUPPORTED
    amb = classify_market("Will the 2026 Senate race be close?")
    assert amb.category == AMBIGUOUS
    assert not amb.auto_publishable()
