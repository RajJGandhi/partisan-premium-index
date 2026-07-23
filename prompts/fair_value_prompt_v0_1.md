# Fair Value Prompt v0.1

Purpose: estimate the blind fair probability that one option-level Polymarket contract resolves YES.

Rules:
- The LLM does not receive market price, best bid, best ask, spread, volume, liquidity, or order-book data.
- The LLM receives contract identity, region/bucket/system labels, and evidence packets.
- The LLM returns JSON only.
- Main output is `fair_value`, a number from 0 to 1.
- `confidence` measures confidence in the estimate, not probability of outcome.
- `should_abstain` is allowed when evidence is too weak, but a best estimate is still recorded.

Output schema:

```json
{
  "fair_value": 0.42,
  "confidence": 0.61,
  "should_abstain": false,
  "rationale_short": "Short explanation.",
  "key_uncertainties": ["uncertainty 1", "uncertainty 2"],
  "base_rate_notes": "Relevant base rate."
}
```
