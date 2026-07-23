# Order Book Price Policy

The experiment compares LLM fair-value estimates to executable Polymarket CLOB prices.

For each option-level contract:

1. If both best bid and best ask exist:
   - `comparison_price = (best_bid + best_ask) / 2`
   - `price_type = mid`

2. If no best bid exists but best ask exists:
   - `comparison_price = best_ask`
   - `price_type = ask_only`
   - The row remains usable because the ask is an executable buy price, but it is flagged as lower-liquidity.

3. If no best ask exists:
   - The row is skipped for signal generation at that timestamp.
   - Reason: no executable buy price exists.

Liquidity flags:
- `wide_spread` if spread >= 0.10
- `thin_bid` if bid_depth_3c < 10
- `thin_ask` if ask_depth_3c < 10
- `no_bids` if bid side is empty

The policy was set before LLM fair-value performance was evaluated.
