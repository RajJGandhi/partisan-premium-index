from __future__ import annotations

from app.blind.web_evidence import clean_news_for_bundle, collect_race_news, persist_news
from app.db.models_quant import RaceNewsItem


def _search_fn(query, *, max_results):
    return [
        {"title": "Democrat clinches Senate nomination in Texas", "url": "https://apnews.com/x",
         "snippet": "The state party confirmed the nominee on Tuesday.", "published_at": "2026-08-20"},
        {"title": "Prediction markets swing toward the incumbent", "url": "https://electionbettingodds.com/y",
         "snippet": "Polymarket now prices the race at 62 cents."},
        {"title": "Analysis: betting odds shift after debate", "url": "https://somenews.example/z",
         "snippet": "The betting odds moved five points overnight."},
        {"title": "Governor endorses challenger in TX Senate race", "url": "https://reuters.com/w",
         "snippet": "A high-profile endorsement landed Wednesday."},
    ]


def test_collects_and_scans(monkeypatch):
    items = collect_race_news(
        race_id="tx-sen-2026", state="TX", office="senate", dem_candidate="Jane D",
        rep_candidate="John R", cycle=2026, search_fn=_search_fn, max_searches=1,
    )
    by_status = {}
    for it in items:
        by_status.setdefault(it.contamination_status, []).append(it.title)
    assert any("clinches" in t for t in by_status.get("CLEAN", []))
    assert any("endorses" in t for t in by_status.get("CLEAN", []))
    assert any("electionbettingodds" not in (i.url or "") for i in items)
    assert "BLOCKED" in by_status  # electionbettingodds.com
    assert "QUARANTINED" in by_status  # "betting odds shift"


def test_only_clean_items_reach_the_bundle():
    items = collect_race_news(
        race_id="tx-sen-2026", state="TX", office="senate", dem_candidate="Jane D",
        rep_candidate="John R", cycle=2026, search_fn=_search_fn, max_searches=1,
    )
    entries = clean_news_for_bundle(items)
    assert entries and all("blocked" not in (e.get("title") or "").lower() for e in entries)
    assert all("prediction market" not in (e.get("title") or "").lower() for e in entries)


def test_no_search_fn_returns_empty():
    assert collect_race_news(
        race_id="x", state="TX", office="senate", dem_candidate=None, rep_candidate=None,
        cycle=2026, search_fn=None,
    ) == []


def test_categorization():
    items = collect_race_news(
        race_id="tx-sen-2026", state="TX", office="senate", dem_candidate="Jane D",
        rep_candidate="John R", cycle=2026, search_fn=_search_fn, max_searches=1,
    )
    cats = {it.category for it in items if it.contamination_status == "CLEAN"}
    assert "status" in cats or "endorsement" in cats


def test_persist_dedups(quant_db):
    items = collect_race_news(
        race_id="tx-sen-2026", state="TX", office="senate", dem_candidate="Jane D",
        rep_candidate="John R", cycle=2026, search_fn=_search_fn, max_searches=1,
    )
    with quant_db() as s:
        n1 = persist_news(s, "tx-sen-2026", items)
        s.commit()
        n2 = persist_news(s, "tx-sen-2026", items)
        s.commit()
        assert n1 == len(items) and n2 == 0
        # contamination status is stored (blocked/quarantined rows kept for the record)
        statuses = {r.contamination_status for r in s.query(RaceNewsItem)}
        assert "BLOCKED" in statuses
