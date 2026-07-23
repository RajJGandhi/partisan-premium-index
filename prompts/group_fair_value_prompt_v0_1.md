# Group Fair Value Prompt v0.1

Purpose: allocate probability mass across all options in a parent market at once.

This solves the row-by-row incoherence problem where the LLM gives every option a plausible standalone probability and the total probability exceeds 1.00.

Core rules:
- No market prices are shown to the model.
- The model receives all option labels and contract questions for one parent group.
- The model must return probabilities that sum to approximately 1.00.
- Raw allocation sum is recorded.
- Normalized allocation is used for analysis if needed.

Output schema:

```json
{
  "group_confidence": 0.55,
  "group_rationale_short": "Short group-level rationale.",
  "key_uncertainties": ["uncertainty 1", "uncertainty 2"],
  "allocations": [
    {
      "tracking_id": "RSO-0005",
      "fair_value": 0.05,
      "rationale_short": "Short outcome-specific note."
    }
  ]
}
```
