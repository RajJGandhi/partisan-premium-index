"""Decision Desk HQ Results API v4 election-history provider (state partisan lean).

The provider reads ``GET /api/v4/race-calls`` -- one object per race carrying both a
``candidates`` array (with ``party_id`` / ``party_name``) and ``topline_results.votes``
({candidate_id: count}) -- and turns it into per-state + national Dem-minus-Rep margins.
"""

from __future__ import annotations

import pytest

from app.providers.election_history import (
    DecisionDeskHqElectionHistoryProvider,
    _candidate_party_map,
    _races_from_payload,
)


def _race(race_id, year, state, dem_votes, rep_votes, *, district=None, test_data=False):
    return {
        "race_id": race_id,
        "year": year,
        "state": state,
        "office_id": 1,
        "name": "General Election",
        "district": district,
        "test_data": test_data,
        "candidates": [
            {"cand_id": 100 + race_id, "party_id": 1, "party_name": "Democratic",
             "first_name": "Dee", "last_name": "Dem"},
            {"cand_id": 200 + race_id, "party_id": 2, "party_name": "Republican",
             "first_name": "Ray", "last_name": "Rep"},
            {"cand_id": 300 + race_id, "party_id": 4, "party_name": "Libertarian",
             "first_name": "Lee", "last_name": "Lib"},
        ],
        "topline_results": {
            "votes": {
                str(100 + race_id): dem_votes,
                str(200 + race_id): rep_votes,
                str(300 + race_id): 1_000,
            },
            "total_votes": dem_votes + rep_votes + 1_000,
        },
    }


PAGE = {
    "total": 4,
    "page": 1,
    "limit": 250,
    "total_pages": 1,
    "next_page_url": None,
    "data": [
        _race(1, 2024, "PA", 3_000_000, 3_150_000),          # Trump +2.44
        _race(2, 2024, "CA", 9_000_000, 6_000_000),          # Biden/Harris +20
        _race(3, 2024, "ME", 5_000, 4_000, district="1"),    # ME-01 split -> must be ignored
        _race(4, 2024, "ZZ", 10, 10, test_data=True),        # test_data -> must be ignored
    ],
}


class _FakeDDHQ(DecisionDeskHqElectionHistoryProvider):
    """Stub the OAuth exchange + HTTP so the test exercises only parsing/aggregation."""

    def enabled(self) -> bool:
        return True

    def _bearer(self) -> str:  # no network
        return "test-token"

    def _http_get_json(self, url, params=None, headers=None):
        assert headers and headers.get("Authorization") == "Bearer test-token"
        assert "race-calls" in url
        # single page for whichever year is asked
        return PAGE, 200


def test_race_calls_normalizes_state_and_national_margins(quant_db):
    with quant_db() as s:
        res = _FakeDDHQ(years=(2024,), backoff_base_seconds=0).fetch(s)
    rows = {(r.jurisdiction, r.year): r for r in res.normalized_payload}

    # district row + test_data row dropped
    assert set(rows) == {("PA", 2024), ("CA", 2024), ("US", 2024)}

    pa = rows[("PA", 2024)]
    assert pa.dem_votes == 3_000_000 and pa.rep_votes == 3_150_000
    assert pa.dem_margin_pct == pytest.approx(100 * (3_000_000 - 3_150_000) / 6_150_000)

    # national = sum of the (non-district, non-test) state tallies, third parties excluded
    us = rows[("US", 2024)]
    assert us.dem_votes == 12_000_000 and us.rep_votes == 9_150_000
    assert us.dem_margin_pct == pytest.approx(100 * (12_000_000 - 9_150_000) / 21_150_000)
    assert us.provider == "decisiondesk_election_history"


def test_disabled_without_credentials():
    p = DecisionDeskHqElectionHistoryProvider(years=(2024,))
    # no client id/secret and no static bearer in the test env
    assert p.enabled() is False


def test_helpers_are_defensive():
    assert _races_from_payload({"data": [{"x": 1}, "nope", 3]}) == [{"x": 1}]
    assert _races_from_payload([{"a": 1}]) == [{"a": 1}]
    assert _races_from_payload("garbage") == []

    race = {"candidates": [
        {"cand_id": 7, "party_id": 1},
        {"candidate_id": 8, "party_name": "Republican"},
        {"id": 9, "party": "GRN"},           # unknown party -> omitted, never guessed
        {"party_id": 2},                      # no id -> skipped
    ]}
    assert _candidate_party_map(race) == {"7": "DEM", "8": "REP"}
