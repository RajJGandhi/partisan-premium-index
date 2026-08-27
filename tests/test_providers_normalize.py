from __future__ import annotations

from datetime import date

from app.providers.normalize import (
    bucket_pollster_grade,
    canonical_office,
    canonical_race_id,
    detect_internal,
    detect_partisan_sponsor,
    last_name,
    normalize_name,
    normalize_population,
    poll_content_hash,
)


def test_population_map():
    assert normalize_population("lv") == "LV"
    assert normalize_population("Likely Voters") == "LV"
    assert normalize_population("rv") == "RV"
    assert normalize_population("registered voters") == "RV"
    assert normalize_population("adults") == "A"
    assert normalize_population("all") == "A"
    assert normalize_population("") is None
    assert normalize_population("weird") is None


def test_grade_bucket():
    assert bucket_pollster_grade("A-") == "A"
    assert bucket_pollster_grade("A+") == "A"
    assert bucket_pollster_grade("B/C") == "B"
    assert bucket_pollster_grade("C+") == "C"
    assert bucket_pollster_grade("D-") == "C"
    assert bucket_pollster_grade(None) is None
    assert bucket_pollster_grade("") is None


def test_partisan_and_internal_detection():
    assert detect_partisan_sponsor("Smith for Senate") is not None
    assert detect_partisan_sponsor("Senate Leadership Fund") is not None
    assert detect_partisan_sponsor("Republican State Committee") is not None
    assert detect_partisan_sponsor("The New York Times") is None
    assert detect_partisan_sponsor(None) is None
    assert detect_internal("Internal poll released by the campaign") is True
    assert detect_internal("Marist College") is False


def test_name_helpers():
    assert normalize_name("Sen. John Q. Public Jr.") == "john q public"
    assert last_name("Maria X. Cantwell") == "cantwell"
    assert normalize_name("") == ""


def test_canonical_ids():
    assert canonical_race_id("NC", "Senate", 2026) == "nc-sen-2026"
    assert canonical_race_id("mi", "governor", 2026) == "mi-gov-2026"
    assert canonical_race_id("GA", "US Senate", 2026) == "ga-sen-2026"
    assert canonical_office("U.S. Senate") == "senate"
    assert canonical_office("governor") == "governor"
    assert canonical_office("gov") == "governor"


def test_poll_content_hash_is_stable_and_release_identifying():
    kw = dict(
        pollster="Marist", start_date=date(2026, 8, 1), end_date=date(2026, 8, 4),
        sample_size=900, population="LV", dem_pct=51.0, rep_pct=45.0,
        dem_candidate="Jane Dem", rep_candidate="John Rep", race_id="nc-sen-2026",
    )
    h1 = poll_content_hash(**kw)
    h2 = poll_content_hash(**{**kw, "pollster": "  marist "})  # normalization -> same release
    assert h1 == h2
    h3 = poll_content_hash(**{**kw, "dem_pct": 52.0})  # different numbers -> different release
    assert h3 != h1
