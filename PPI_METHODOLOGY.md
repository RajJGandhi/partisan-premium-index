# PPI Methodology

## Core measure

For every tracked YES outcome:

```text
Partisan premium = standardized Polymarket probability − published PPI fair value
```

A positive premium means Polymarket trades above PPI fair value. A negative premium means it trades below.

## Market-price policy

The system prioritizes executable and reproducible CLOB data:

1. if best bid and ask exist, comparison probability is their midpoint;
2. executable buy price is the best ask;
3. executable sell price is the best bid;
4. if only an ask exists, the comparison is labelled `ask_only`;
5. if no book price exists but an official last trade is present, it is labelled `last_trade`;
6. absent or stale pricing produces a visible stale/unavailable state.

The snapshot also preserves spread, depth, volume, liquidity and raw order-book data.

## Fair-value components

Default weights:

| Component | Weight |
|---|---:|
| Polling | 35% |
| Forecast/fundamentals | 25% |
| Comparable markets | 20% |
| Expert/race-rating consensus | 10% |
| Campaign/news judgment | 10% |

Weights are editable per market and must total 100%.

## Missing values

A missing component is never silently replaced with 50% or another invented value.

- Missing components marked eligible for redistribution have their weight redistributed proportionally across available components.
- Original and effective weights are both stored and displayed.
- Missing components trigger human review.
- A non-redistributable missing component generates an explicit warning.

Every component records:

- probability;
- source label and URL;
- observation timestamp;
- ingestion method;
- notes.

## Automated versus human judgment

Automation may:

- refresh market prices and objective source observations;
- discover evidence;
- classify relevance;
- calculate a proposed fair value.

Automation may not silently alter a published fair value.

A substantive change creates a `fair_value_proposals` row. An administrator may approve, edit or reject it. Approval creates an immutable `fair_value_revisions` row with the components, weights, evidence, thesis, justification, timestamp and publisher.

## Evidence relevance

An item is relevant only when it could materially affect:

- outcome probability;
- resolution;
- polling;
- ballot access;
- candidacy;
- election rules;
- a major forecast input.

A mention of a candidate, office, country or party is insufficient by itself.

## Aggregate index

The overview publishes several statistics so offsetting values are not hidden:

- equal-weighted average signed premium;
- equal-weighted average absolute premium;
- liquidity-weighted signed premium;
- share of markets trading above PPI fair value.

The equal-weighted signed mean is labelled provisional while the production sample remains small and selected.

## Prediction ledger

The first approved fair value records:

- publication timestamp;
- PPI fair value;
- contemporaneous market probability;
- thesis;
- evidence.

Later changes append revisions rather than overwriting history.

At resolution:

```text
Brier score = (forecast probability − binary outcome)²
```

The ledger stores PPI Brier score, market Brier score and market-minus-PPI performance difference.

## Historical data

Official Polymarket history may be backfilled, but prelaunch rows are labelled `market_price_only`. No historical PPI value is inferred or back-created.

## Interpretation limits

- The market universe is manually selected, not a representative census.
- Human judgment remains present in component selection and approval.
- Liquidity, resolution ambiguity and stale sources can affect comparability.
- LLM classification is a triage tool, not an authority.
- Early track-record and calibration results are descriptive until the resolved sample is large enough.
