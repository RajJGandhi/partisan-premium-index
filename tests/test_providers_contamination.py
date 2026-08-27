from __future__ import annotations

from app.providers.contamination import (
    BLOCKED,
    CLEAN,
    QUARANTINED,
    PredictionMarketContaminationScanner,
)


def test_blocked_domains():
    sc = PredictionMarketContaminationScanner()
    for url in (
        "https://polymarket.com/event/senate-2026",
        "https://www.kalshi.com/markets/xyz",
        "http://predictit.org/markets/detail/123",
        "https://manifold.markets/q/abc",
        "https://electionbettingodds.com/",
        "https://metaculus.com/questions/999/",
    ):
        r = sc.scan(url=url)
        assert r.status == BLOCKED
        assert r.blocked_source and not r.usable_for_blind_forecast


def test_quarantines_market_language_in_body():
    sc = PredictionMarketContaminationScanner()
    r = sc.scan(
        title="Senate race tightens",
        text="Prediction markets now give the Democrat a 62% chance; the contract is trading at 62 cents.",
        url="https://apnews.com/article/senate",
    )
    assert r.status == QUARANTINED
    assert "prediction markets" in r.reason.lower()
    assert not r.usable_for_blind_forecast
    assert r.hits


def test_clean_news_passes():
    sc = PredictionMarketContaminationScanner()
    r = sc.scan(
        title="Governor signs budget",
        text="The governor signed the state budget on Tuesday after a bipartisan vote in the legislature.",
        url="https://reuters.com/world/us/",
    )
    assert r.status == CLEAN
    assert r.usable_for_blind_forecast


def test_betting_odds_phrases_quarantined():
    sc = PredictionMarketContaminationScanner()
    for text in ("the betting odds shifted sharply", "bookmakers moved the moneyline", "election odds favour the incumbent"):
        assert sc.scan(text=text, url="https://example.com").status == QUARANTINED


def test_extra_blocked_domains_are_honoured():
    sc = PredictionMarketContaminationScanner(extra_blocked_domains={"sketchybets.example"})
    assert sc.scan(url="https://sketchybets.example/x").status == BLOCKED
