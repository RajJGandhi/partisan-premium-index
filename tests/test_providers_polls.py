from __future__ import annotations

from app.providers.polls import (
    DecisionDeskHqPollProvider,
    PollingSourcePollProvider,
    VoteHubRacePollProvider,
    polls_to_observations,
)
from app.providers.race_identity import KnownRace

# --- DDHQ ballot_test: one row per candidate; must group into head-to-head polls ---------------
DDHQ_ROWS = [
    {"poll_id": 1, "question_id": 10, "pollster": "Siena", "sponsor": "New York Times",
     "start_date": "2026-08-12", "end_date": "2026-08-16", "sample_size": 800, "population": "lv",
     "created_at": "2026-08-16T00:00:00Z", "election_type": "General", "cycle": "2026",
     "office_type": "Senate", "state": "NC", "candidate_name": "Jane Democrat", "pct": 49},
    {"poll_id": 1, "question_id": 10, "pollster": "Siena", "sponsor": "New York Times",
     "start_date": "2026-08-12", "end_date": "2026-08-16", "sample_size": 800, "population": "lv",
     "created_at": "2026-08-16T00:00:00Z", "election_type": "General", "cycle": "2026",
     "office_type": "Senate", "state": "NC", "candidate_name": "John Republican", "pct": 45},
    # a primary poll -> must be excluded
    {"poll_id": 2, "question_id": 20, "pollster": "PPP", "start_date": "2026-05-01", "end_date": "2026-05-03",
     "sample_size": 500, "population": "rv", "created_at": "2026-05-03T00:00:00Z", "election_type": "Primary",
     "cycle": "2026", "office_type": "Senate", "state": "NC", "candidate_name": "Jane Democrat", "pct": 60},
    # a house poll -> must be excluded (not senate/governor)
    {"poll_id": 3, "question_id": 30, "pollster": "X", "start_date": "2026-08-01", "end_date": "2026-08-02",
     "sample_size": 400, "created_at": "2026-08-02T00:00:00Z", "election_type": "General", "cycle": "2026",
     "office_type": "House", "state": "NC", "district": 1, "candidate_name": "Someone", "pct": 50},
]


class _FakeDDHQ(DecisionDeskHqPollProvider):
    def _do_fetch(self, **kwargs):
        return DDHQ_ROWS, "https://polling.decisiondeskhq.com/api/v1/polls/ballot_test", 200


def test_ddhq_normalizer_groups_candidate_rows_and_filters(quant_db):
    with quant_db() as s:
        res = _FakeDDHQ(cycle=2026, backoff_base_seconds=0).fetch(s)
    polls = res.normalized_payload
    assert len(polls) == 1  # only the general Senate head-to-head
    p = polls[0]
    assert p.pollster == "Siena" and p.state == "NC" and p.office == "senate"
    assert p.population == "LV" and p.sample_size == 800
    names = {a["name"] for a in p.answers}
    assert names == {"Jane Democrat", "John Republican"}


def test_polls_to_observations_resolves_party_and_dedups():
    provider = _FakeDDHQ(cycle=2026, backoff_base_seconds=0)
    polls = provider._normalize(DDHQ_ROWS)
    known = [KnownRace("nc-sen-2026", "NC", "senate", 2026, "Jane Democrat", "John Republican")]
    rows, skipped = polls_to_observations(polls + polls, known)  # feed duplicates
    assert len(rows) == 1
    r = rows[0]
    assert r.race_id == "nc-sen-2026"
    assert r.dem_pct == 49 and r.rep_pct == 45 and r.margin_dem == 4
    assert r.dem_candidate == "Jane Democrat" and r.rep_candidate == "John Republican"
    assert any(sk["reason"] == "duplicate_release" for sk in skipped)


def test_partisan_sponsor_downweight_marked_not_dropped():
    rows_in = [
        {**DDHQ_ROWS[0], "sponsor": "Democrats for NC PAC"},
        {**DDHQ_ROWS[1], "sponsor": "Democrats for NC PAC"},
    ]
    provider = _FakeDDHQ(cycle=2026, backoff_base_seconds=0)
    polls = provider._normalize(rows_in)
    known = [KnownRace("nc-sen-2026", "NC", "senate", 2026, "Jane Democrat", "John Republican")]
    rows, skipped = polls_to_observations(polls, known)
    assert len(rows) == 1 and not skipped
    assert rows[0].partisan_sponsor is not None  # flagged, still ingested


def test_unmatched_race_is_skipped_with_reason():
    provider = _FakeDDHQ(cycle=2026, backoff_base_seconds=0)
    polls = provider._normalize(DDHQ_ROWS)
    rows, skipped = polls_to_observations(polls, known_races=[])  # no known races
    assert rows == []
    assert skipped and skipped[0]["reason"] == "unmatched_race"


def test_pollingsource_provider_disabled_without_base_url(quant_db):
    with quant_db() as s:
        res = PollingSourcePollProvider(cycle=2026, backoff_base_seconds=0).fetch(s)
    assert res.status == "EMPTY"
    assert not res.usable


# --- VoteHub race polls: state comes from the `subject`; primaries are dropped -----------------
VOTEHUB_ROWS = {
    "us-senator": [
        {"id": "a1", "poll_type": "us-senator", "subject": "2026 North Carolina",
         "pollster": "Change Research", "start_date": "2026-09-01", "end_date": "2026-09-08",
         "sample_size": 900, "population": "lv",
         "answers": [{"choice": "Roy Cooper", "pct": 48.0}, {"choice": "Michael Whatley", "pct": 41.0}],
         "sponsors": [], "internal": False, "partisan": None, "url": "https://example.com/nc"},
        {"id": "a2", "poll_type": "us-senator", "subject": "2026 Texas Democratic",  # primary -> drop
         "pollster": "Slingshot", "start_date": "2026-01-14", "end_date": "2026-01-21",
         "sample_size": 1290, "population": "lv",
         "answers": [{"choice": "Jasmine Crockett", "pct": 38.0}, {"choice": "James Talarico", "pct": 37.0}],
         "sponsors": ["Texas Public Opinion Research"], "internal": False, "partisan": None},
        {"id": "a3", "poll_type": "us-senator", "subject": "2026 Freedonia",  # unknown state -> drop
         "pollster": "X", "start_date": "2026-09-01", "end_date": "2026-09-02", "sample_size": 500,
         "answers": [{"choice": "A", "pct": 50.0}, {"choice": "B", "pct": 45.0}]},
    ],
    "governor": [
        {"id": "g1", "poll_type": "governor", "subject": "2026 Arizona",
         "pollster": "Noble Predictive", "start_date": "2026-08-20", "end_date": "2026-08-25",
         "sample_size": 800, "population": "rv",
         "answers": [{"choice": "Katie Hobbs", "pct": 46.0}, {"choice": "Karrin Taylor Robson", "pct": 45.0}],
         "sponsors": [], "internal": False, "partisan": "REP"},
    ],
}


class _FakeVoteHubRace(VoteHubRacePollProvider):
    def _http_get_json(self, url, params=None, headers=None):
        return list(VOTEHUB_ROWS[params["poll_type"]]), 200


def test_votehub_race_polls_parse_subject_and_drop_primaries(quant_db):
    with quant_db() as s:
        res = _FakeVoteHubRace(cycle=2026, backoff_base_seconds=0).fetch(s)
    polls = {(p.state, p.office): p for p in res.normalized_payload}
    assert set(polls) == {("NC", "senate"), ("AZ", "governor")}  # TX primary + Freedonia dropped
    nc = polls[("NC", "senate")]
    assert nc.cycle == 2026 and nc.election_type == "General"
    assert {a["name"] for a in nc.answers} == {"Roy Cooper", "Michael Whatley"}
    assert polls[("AZ", "governor")].sponsor == "Republican-aligned"


def test_votehub_race_polls_feed_polls_to_observations(quant_db):
    with quant_db() as s:
        res = _FakeVoteHubRace(cycle=2026, backoff_base_seconds=0).fetch(s)
    known = [KnownRace("nc-sen-2026", "NC", "senate", 2026, "Roy Cooper", "Michael Whatley")]
    rows, skipped = polls_to_observations(res.normalized_payload, known)
    assert len(rows) == 1
    assert rows[0].race_id == "nc-sen-2026"
    assert rows[0].dem_pct == 48.0 and rows[0].rep_pct == 41.0
    assert any(sk["reason"] == "unmatched_race" for sk in skipped)  # the AZ governor poll


def test_votehub_race_state_parser():
    f = VoteHubRacePollProvider._state_from_subject
    assert f("2026 North Carolina") == ("NC", False)
    assert f("2026 Texas Democratic") == ("TX", True)
    assert f("2026 Georgia Republican") == ("GA", True)
    assert f("2026 Freedonia") == (None, False)
