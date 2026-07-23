from pathlib import Path

EXAMPLE = """market_id,question,polymarket_slug,polling_prob,forecast_prob,other_markets_prob,expert_prob,news_campaign_prob,manual_fair_yes,confidence,source_notes,last_updated
example-1,Will Republicans win the Senate in 2026?,republicans-senate-2026,0.51,0.54,0.55,0.52,0.53,,0.70,Example placeholder values. Replace before using.,2026-06-02
example-2,Will Democrats win the House in 2026?,democrats-house-2026,0.55,0.56,0.54,0.57,0.55,,0.72,Example placeholder values. Replace before using.,2026-06-02
"""

if __name__ == "__main__":
    path = Path("data/fair_values.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE, encoding="utf-8")
    print(f"Wrote {path}")
