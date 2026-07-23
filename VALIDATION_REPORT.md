# PPI Validation Report

Validated on **2026-07-20 UTC**.

## Release state

- Clean SQLite database created through `scripts/migrate_db.py`.
- Production seed applied twice to verify idempotency.
- Final database contains 12 enabled market configurations and 48 source configurations.
- Final database contains **no** snapshots, evidence, fair-value components, published fair values, proposals, predictions or job runs.
- No `.env`, private key, wallet key or production credential is included.
- Demonstration component values were not seeded.

## Commands executed

```bash
PYTHONPATH=. python scripts/migrate_db.py
PYTHONPATH=. python scripts/seed_production_markets.py
make validate
```

`make validate` completed successfully:

```text
Ruff: all checks passed
Mypy: success, no issues in 16 checked source files
Pytest: 33 passed
Compileall: passed
```

The test suite covers:

- premium and weighted-fair-value calculations;
- missing-component redistribution and weight validation;
- classifier schema validation and deterministic fallback;
- evidence deduplication;
- SSRF URL checks and redirect revalidation;
- canonical daily-snapshot idempotency;
- stale timestamp handling;
- mocked Gamma/CLOB integration and price policy;
- database schema creation;
- production seed/source-pack integrity;
- managed PostgreSQL URL normalization;
- immutable administrative approval and prediction-ledger creation;
- daily digest generation;
- public Streamlit application smoke boot.

## Application health smoke test

The Streamlit server was started headlessly and its health endpoint returned:

```text
ok
STREAMLIT_HEALTH_OK
```

## Live external API validation

The sandbox could not resolve external DNS names during release validation:

```text
gamma-api.polymarket.com  Temporary failure in name resolution
clob.polymarket.com       Temporary failure in name resolution
news.google.com           Temporary failure in name resolution
```

Therefore, this environment could not complete a genuine live ingestion run. The Polymarket integration is covered by mocked integration tests and is implemented against the documented public Gamma market, CLOB order-book and price-history endpoints. Run the initial production pipeline in a normal networked environment before publishing live data.

## Deployment validation boundary

Docker is not installed in the build sandbox, so an image build was not executed here. The delivered repository includes a Dockerfile, Compose configuration, Render blueprint, health check and CI workflow. Python application/runtime validation was executed directly.
