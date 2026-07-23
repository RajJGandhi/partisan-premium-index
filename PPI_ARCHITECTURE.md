# PPI Architecture

## System shape

```text
Public/admin Streamlit application
            │
            ├── SQLAlchemy domain layer
            │       ├── SQLite (local)
            │       └── PostgreSQL (production)
            │
            ├── Daily pipeline / scheduler
            │       ├── Polymarket Gamma metadata
            │       ├── Polymarket CLOB order books/history
            │       ├── Evidence adapters
            │       ├── Relevance classifier abstraction
            │       ├── Proposal generator
            │       └── Canonical snapshots/index
            │
            └── Immutable publication/performance ledger
```

## Why the existing stack was preserved

The repository already used Python, SQLAlchemy, SQLite, Streamlit, Ollama and scheduled jobs. Replacing it with a Cloudflare-native TypeScript stack would have discarded working scoring, CLOB, LLM and paper-testing code. The production path therefore uses the native Python equivalents:

- Docker web service for Streamlit;
- Docker background worker for APScheduler;
- PostgreSQL for shared durable production state;
- SQLite for a single-process local installation.

## Main modules

### `app/ppi/polymarket.py`

- fetches a tracked Gamma market by ID or slug;
- fetches public CLOB books using token IDs;
- saves raw responses and hashes;
- applies the standardized price policy;
- supports official historical-price backfilling.

### `app/ppi/evidence.py`

Adapters:

- RSS/Atom;
- Google News RSS query;
- GDELT document discovery;
- configurable JSON/API feeds;
- manual observations;
- manually entered external-market observations.

Evidence is deduplicated per market using normalized title, canonical URL and content hash.

### `app/ppi/security.py`

- enforces HTTPS by default;
- rejects credentials embedded in URLs;
- rejects localhost, private, link-local, reserved and multicast addresses;
- supports optional domain allowlists;
- strips common tracking query parameters;
- verifies bcrypt passwords server-side.

### `app/ppi/classifier.py`

Provider interface:

- deterministic fallback;
- Ollama/Qwen;
- OpenAI-compatible chat-completion API.

All model responses validate against `EvidenceClassification`. Malformed or failed responses automatically fall back to deterministic classification and cannot crash the daily job.

### `app/ppi/methodology.py`

- validates probabilities and weights;
- calculates weighted fair values;
- preserves original and effective weights;
- handles missing-component redistribution explicitly;
- calculates partisan premium and Brier scores.

### `app/ppi/publication.py`

- approves/rejects proposals;
- creates immutable fair-value revisions;
- creates the initial prediction-ledger entry;
- records resolutions and performance.

### `app/ppi/pipeline.py`

One UTC daily run:

1. starts a `job_runs` record;
2. syncs enabled markets;
3. records source-level runs;
4. collects and classifies evidence;
5. proposes—but never silently publishes—fair-value changes;
6. upserts one canonical daily market snapshot;
7. writes the daily aggregate index;
8. records sanitized failures;
9. writes a durable Markdown/JSON daily digest with market movements, evidence, proposals and failures;
10. optionally sends a compact Discord digest with a direct approval-queue link.

The pipeline commits after each market so a process interruption preserves completed work. A forced rerun updates the same daily snapshot instead of creating duplicates.

## Data model

Required production entities:

- `markets`
- `market_sources`
- `market_snapshots`
- `raw_market_responses`
- `evidence_items`
- `fair_value_components`
- `fair_value_proposals`
- `fair_value_revisions`
- `predictions`
- `market_resolutions`
- `daily_index`
- `job_runs`
- `source_runs`
- `admin_users`

Legacy Reality Spread entities remain available for backward compatibility.

## Reliability

- exponential retries on Polymarket, RSS, GDELT and JSON source calls;
- SSRF-safe redirect validation for administrator-configured source URLs;
- source and request timeouts;
- source-level failure isolation;
- incremental commits;
- primary and backup daily runs;
- evidence and snapshot uniqueness constraints;
- explicit `STALE`, `PARTIAL` and `FAILED` states;
- no raw stack traces in public status views.

## Security boundary

The browser never receives LLM keys, database credentials, admin hashes or webhook URLs. All source fetching, classification, publication and administration occur server-side.
