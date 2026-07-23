from app.alerts.discord import format_discord_alert, send_discord_alert


def test_format_discord_alert_includes_core_fields():
    message = format_discord_alert(
        {
            "question": "Will example happen?",
            "action": "alert",
            "ppi_score": 82,
            "polymarket_yes": 0.61,
            "fair_yes": 0.49,
            "premium_points": 12,
            "warnings": ["Example warning"],
        }
    )
    assert "Reality Spread Alert" in message
    assert "82/100" in message
    assert "Example warning" in message


def test_missing_webhook_does_not_crash():
    ok, error = send_discord_alert("hello", webhook_url="")
    assert ok is False
    assert error
