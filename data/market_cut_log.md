# Market Cut Log

## Experiment Universe Freeze

The initial market universe began as 40 parent market concepts and was expanded into option-level Polymarket contracts. Each option-level row represents a specific tradable outcome contract rather than a broad event label.

The final v0.1 forward-test universe uses only contracts that were verified as executable through Polymarket/Gamma/CLOB metadata.

## Final Frozen Universe

- Final file: `data/tracked_markets_final.csv`
- Final universe: 188 verified executable option-level contracts
- Freeze timing: before forward-test results were collected
- Inclusion requirement:
  - `verification_status = VERIFIED_READY`
  - `active = true`
  - `closed = false`
  - `enable_order_book = true`
  - CLOB token IDs present
  - row matched to a real Polymarket contract
  - no known wrong-topic match

## Major Cuts

The following market groups or option rows were cut before the forward test:

### Brazil Presidential Election First Round: 1st Place

Removed because no reliable live Polymarket event was found during automated and slug-ladder verification.

### Florida Governor Republican Primary / Fishback

Removed because the resolver matched these rows to unrelated markets and no reliable live CLOB-backed event was verified for v0.1.

### Republican Senate Seats: 48–52

Removed because the resolver repeatedly mapped these rows to the generic Senate-control market rather than exact seat-count bracket contracts.

### Arizona Governor

Removed from v0.1 because no reliable live executable contract was verified through resolver, topic gates, or slug-ladder lookup.

### São Paulo Governor

Removed from v0.1 because no reliable live executable contract was verified through resolver, topic gates, or slug-ladder lookup.

### Next Premier of Quebec

Removed from v0.1 because no reliable live executable contract was verified through resolver, topic gates, or slug-ladder lookup.

### Toronto Mayor

Removed from v0.1 because no reliable live executable contract was verified through resolver, topic gates, or slug-ladder lookup.

### Problematic Brazil Margin Rows

Rows were removed when they duplicated another margin contract or failed to resolve to the intended bracket.

## Methodological Note

All cuts were made before the forward-test period and before evaluating any LLM-vs-market performance results. The purpose of the cuts was data integrity, not result selection.

The final sample is therefore defined as the set of verified, executable, CLOB-backed contracts available at freeze time.
