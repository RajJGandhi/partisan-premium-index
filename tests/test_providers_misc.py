"""National environment, election history, candidates, and market classification providers."""

from __future__ import annotations

from pathlib import Path

from app.providers.candidates import (
    OpenFecCandidateProvider,
    WikipediaCandidateProvider,
    _clean_wikitext_name,
    _looks_like_person_name,
    _parse_election_infobox,
    _party,
)
from app.providers.election_history import SeedCsvElectionHistoryProvider
from app.providers.markets import (
    AMBIGUOUS,
    SUPPORTED_HOUSE_CONTROL,
    SUPPORTED_SENATE_CONTROL,
    SUPPORTED_STATEWIDE_RACE,
    UNSUPPORTED,
    classify_market,
)
from app.providers.national_environment import (
    DecisionDeskHqGenericBallotProvider,
    VoteHubGenericBallotProvider,
    _int,
)

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


# --- VoteHub generic ballot: live shape is answers=[{choice,pct}] + sponsors[]/internal/partisan
class _FakeVoteHubGeneric(VoteHubGenericBallotProvider):
    ROWS = {
        "polls": [
            {"id": "gen1", "poll_type": "generic-ballot", "sample_size": 1500, "population": "lv",
             "pollster": "Cygnal", "start_date": "2026-08-04", "end_date": "2026-08-06",
             "answers": [{"choice": "Dem", "pct": 46.2}, {"choice": "Rep", "pct": 47.2}],
             "sponsors": [], "internal": False, "partisan": None, "url": "https://ex.com/1"},
            {"id": "gen2", "poll_type": "generic-ballot", "sample_size": 1000, "population": "rv",
             "pollster": "Quantus Insights", "start_date": "2026-08-10", "end_date": "2026-08-12",
             "answers": [{"choice": "Democrats", "pct": 44.0}, {"choice": "Republicans", "pct": 48.0}],
             "sponsors": ["TrendingPolitics"], "internal": False, "partisan": "REP"},
        ]
    }

    def _do_fetch(self, **kwargs):
        return self.ROWS["polls"], "https://api.votehub.com/polls", 200


def test_votehub_generic_ballot_reads_choice_pct_and_partisan(quant_db):
    with quant_db() as s:
        res = _FakeVoteHubGeneric(cycle=2026, backoff_base_seconds=0).fetch(s)
    polls = sorted(res.normalized_payload, key=lambda p: p.provider_poll_id)
    assert len(polls) == 2
    assert polls[0].dem_pct == 46.2 and polls[0].rep_pct == 47.2
    assert polls[0].sample_size == 1500
    assert polls[1].dem_pct == 44.0 and polls[1].rep_pct == 48.0
    assert "Republican-aligned" in (polls[1].sponsor or "")  # partisan:"REP" folded into sponsor


def test_int_handles_votehub_string_sample_sizes():
    assert _int("1,024") == 1024
    assert _int("1500") == 1500
    assert _int(1500) == 1500
    assert _int("") is None and _int(None) is None


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


# --- Wikipedia election-infobox candidate provider -------------------------------------------
def _infobox(*pairs, incumbent=None):
    lines = ["{{Infobox election"]
    if incumbent:
        lines.append(f"| incumbent = [[{incumbent}]]")
    for i, (nom, party) in enumerate(pairs, 1):
        lines.append(f"| nominee{i} = {nom}")
        lines.append(f"| party{i} = {party} Party (United States)")
    lines.append("}}")
    return "\n".join(lines)


_WIKI_BATCH = {
    "batchcomplete": True,
    "query": {
        "pages": [
            {"title": "2026 United States Senate election in North Carolina",
             "revisions": [{"slots": {"main": {"content": _infobox(
                 ("[[Roy Cooper]]", "Democratic"), ("[[Michael Whatley]]", "Republican"))}}}]},
            {"title": "2026 Georgia gubernatorial election",
             "revisions": [{"slots": {"main": {"content": _infobox(
                 ("[[Keisha Lance Bottoms]]", "Democratic"), ("[[Burt Jones]]", "Republican"),
                 incumbent="Burt Jones")}}}]},
            {"title": "2026 Kansas gubernatorial election", "missing": True},
        ]
    },
}


class _FakeWiki(WikipediaCandidateProvider):
    def __init__(self, **kw):
        super().__init__(race_configs={
            "nc-sen-2026": {"state": "NC", "cycle": 2026, "office": "senate"},
            "ga-gov-2026": {"state": "GA", "cycle": 2026, "office": "governor"},
            "ks-gov-2026": {"state": "KS", "cycle": 2026, "office": "governor"},
        }, **kw)
        self.http_calls = 0

    def _http_get_json(self, url, params=None, headers=None):
        self.http_calls += 1
        return _WIKI_BATCH, 200


def test_wikipedia_candidates_batched_one_call_for_all_races(quant_db):
    p = _FakeWiki(backoff_base_seconds=0)
    with quant_db() as s:
        a = p.fetch(s, race_id="nc-sen-2026", state="NC", cycle=2026, office="senate")
        s.commit()
        b = p.fetch(s, race_id="ga-gov-2026", state="GA", cycle=2026, office="governor")
        c = p.fetch(s, race_id="ks-gov-2026", state="KS", cycle=2026, office="governor")
    assert p.http_calls == 1  # one batched action=query for the whole run
    assert {r.party: r.name for r in a.normalized_payload} == {"DEM": "Roy Cooper", "REP": "Michael Whatley"}
    ga = {r.party: r for r in b.normalized_payload}
    assert ga["DEM"].name == "Keisha Lance Bottoms" and ga["REP"].name == "Burt Jones"
    assert ga["REP"].is_incumbent is True and ga["DEM"].is_incumbent is False
    assert c.normalized_payload == []  # missing article -> no guessed name


def test_election_infobox_parser_handles_comments_and_replacement_notes():
    wt = _infobox(
        ("[[Susan Collins]]", "Republican"),
        ("[[Troy Jackson]] <!-- MOS note -->(replacing [[Graham Platner]])<!-- unclosed", "Democratic"),
    )
    out = _parse_election_infobox(wt)
    assert out["REP"]["name"] == "Susan Collins"
    assert out["DEM"]["name"] == "Troy Jackson"


def test_election_infobox_parser_rejects_non_names_and_empty_nominees():
    # empty nominee value, party present on the next line -> must not leak "party1 = ..."
    wt = "{{Infobox election\n| nominee1 = \n| party1 = Democratic Party (United States)\n}}"
    assert _parse_election_infobox(wt) == {}
    assert _looks_like_person_name("Roy Cooper") is True
    assert _looks_like_person_name("party1 = Democratic Party") is False
    assert _looks_like_person_name("TBD") is False
    assert _clean_wikitext_name("[[Mike Collins (politician)]]") == "Mike Collins"


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
