import pytest

from app.db.models import Market
from app.ppi.polymarket import TrackedPolymarketClient, price_policy, update_market_from_gamma


def test_mocked_polymarket_market_and_orderbook(monkeypatch):
    market = Market(platform_market_id="123", question="Old", enabled=True, yes_token_id="YES", no_token_id="NO")
    client = TrackedPolymarketClient()

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": "123",
                "question": "Updated question",
                "active": True,
                "closed": False,
                "enableOrderBook": True,
                "volumeNum": 100,
                "liquidityNum": 50,
                "clobTokenIds": '["YES","NO"]',
            }

    monkeypatch.setattr("requests.get", lambda *a, **k: Response())
    data, _, _ = client.fetch_market(market)
    update_market_from_gamma(market, data)
    assert market.question == "Updated question"
    policy = price_policy({"yes_best_bid": 0.45, "yes_best_ask": 0.47})
    assert policy["comparison_price"] == pytest.approx(0.46)
    assert policy["executable_buy_price"] == 0.47
