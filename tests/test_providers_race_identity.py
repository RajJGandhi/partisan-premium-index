from __future__ import annotations

from app.providers.race_identity import (
    ABSTAIN,
    DETERMINISTIC,
    FUZZY,
    LLM,
    KnownRace,
    RaceMatch,
    match_to_race,
    resolve_candidate_party,
)

NC = KnownRace("nc-sen-2026", "NC", "senate", 2026, "Jane Democrat", "John Republican")
GA = KnownRace("ga-sen-2026", "GA", "senate", 2026, "Sam Dem", "Pat Rep")
NC_GOV = KnownRace("nc-gov-2026", "NC", "governor", 2026, "Alex D", "Casey R")


def test_deterministic_unique_key_match():
    m = match_to_race(state="NC", office="senate", cycle=2026, candidate_names=["Jane Democrat", "John Republican"],
                      known_races=[NC, GA, NC_GOV])
    assert m.race_id == "nc-sen-2026"
    assert m.method == DETERMINISTIC
    assert m.confidence >= 0.9


def test_deterministic_match_without_candidate_names():
    m = match_to_race(state="NC", office="senate", cycle=2026, known_races=[NC, GA])
    assert m.race_id == "nc-sen-2026" and m.method == DETERMINISTIC


def test_fuzzy_disambiguates_between_two_same_key_races():
    dup1 = KnownRace("nc-sen-2026-a", "NC", "senate", 2026, "Jane Democrat", "John Republican")
    dup2 = KnownRace("nc-sen-2026-b", "NC", "senate", 2026, "Other Dem", "Other Rep")
    m = match_to_race(state="NC", office="senate", cycle=2026,
                      candidate_names=["Jane Democrat", "John Republican"], known_races=[dup1, dup2])
    assert m.race_id == "nc-sen-2026-a"
    assert m.method == FUZZY


def test_abstains_when_no_known_race():
    m = match_to_race(state="TX", office="senate", cycle=2026, candidate_names=["A", "B"], known_races=[NC, GA])
    assert not m.matched and m.method == ABSTAIN


def test_abstains_when_names_disagree_with_only_candidate_race():
    m = match_to_race(state="NC", office="senate", cycle=2026,
                      candidate_names=["Completely Different", "Nobody Knows"], known_races=[NC])
    # single key race but name overlap < 0.5 -> falls through, no other key race, abstain
    assert not m.matched


def test_llm_resolver_used_only_for_ambiguity_and_respects_threshold():
    dup1 = KnownRace("x-1", "NC", "senate", 2026, "Aaa Bbb", "Ccc Ddd")
    dup2 = KnownRace("x-2", "NC", "senate", 2026, "Eee Fff", "Ggg Hhh")

    def resolver(*, state, office, cycle, candidate_names, candidates):
        return RaceMatch("x-2", 0.93, LLM, "matched on incumbent context", ("https://sos.nc.gov",))

    m = match_to_race(state="NC", office="senate", cycle=2026, candidate_names=["Unknown One", "Unknown Two"],
                      known_races=[dup1, dup2], llm_resolver=resolver)
    assert m.race_id == "x-2" and m.method == LLM and m.citations

    def weak_resolver(**kw):
        return RaceMatch("x-2", 0.4, LLM, "not sure")

    m2 = match_to_race(state="NC", office="senate", cycle=2026, candidate_names=["Unknown One", "Unknown Two"],
                       known_races=[dup1, dup2], llm_resolver=weak_resolver)
    assert not m2.matched and m2.method == ABSTAIN


def test_llm_resolver_error_abstains_never_attaches():
    def boom(**kw):
        raise RuntimeError("api down")

    dup1 = KnownRace("x-1", "NC", "senate", 2026, "Aaa Bbb", "Ccc Ddd")
    dup2 = KnownRace("x-2", "NC", "senate", 2026, "Eee Fff", "Ggg Hhh")
    m = match_to_race(state="NC", office="senate", cycle=2026, candidate_names=["Q", "Z"],
                      known_races=[dup1, dup2], llm_resolver=boom)
    assert not m.matched and "errored" in m.rationale


def test_resolve_candidate_party():
    assert resolve_candidate_party("Jane Democrat", dem_candidate="Jane Democrat", rep_candidate="John Republican") == "DEM"
    assert resolve_candidate_party("J. Republican", dem_candidate="Jane Democrat", rep_candidate="John Republican") == "REP"
    assert resolve_candidate_party("Nobody", dem_candidate="Jane Democrat", rep_candidate="John Republican") is None
