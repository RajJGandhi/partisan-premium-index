# Partisan Premium Index (PPI)

PPI is a transparent, timestamped prediction-market research product. It compares current executable Polymarket probabilities with independently published fair values and records the difference:

```text
Partisan premium = Polymarket implied probability − PPI fair value
```

The repository also preserves the earlier **Reality Spread** forward-testing scripts. PPI and Reality Spread remain methodologically distinct:

- **PPI:** human-supervised weighted fair value with an approval ledger.
- **Reality Spread:** blind LLM-generated fair values compared with market prices.

PPI is not a trading bot. It never accepts wallet keys, places orders or executes real-money transactions.

## Implemented product

### Public application

- Overview with aggregate signed PPI, average absolute premium, tracked outcomes, freshness and largest premiums.
- Filterable market directory.
- Market detail pages with Polymarket-versus-PPI history, components, evidence and immutable revisions.
- Track-record page with prediction ledger, Brier scores and sample-size warnings.
- Methodology and limitations page.
- Public system-status page with sanitized job/source failures.
- Responsive Streamlit interface compatible with light and dark themes.

### Protected administration

Server-side password verification protects the administration section. Administrators can:

- add, edit, enable and disable tracked markets;
- configure source packs, aliases, weights and source adapters;
- edit fair-value component inputs with source provenance;
- submit and review evidence;
- approve, edit or reject proposed fair-value changes;
- trigger/retry ingestion;
- resolve markets and calculate Brier scores;
- inspect sanitized job and source failures.

### Daily pipeline

The idempotent pipeline:

1. refreshes enabled tracked markets through the public Gamma API;
2. fetches executable CLOB order books for YES/NO tokens;
3. preserves raw market responses;
4. collects and deduplicates RSS, Google News, GDELT, JSON/API and manual evidence;
5. classifies relevance through a deterministic fallback, local Ollama/Qwen or an OpenAI-compatible provider;
6. calculates proposed fair values from visible components and effective weights;
7. places substantive changes in an approval queue;
8. writes one canonical UTC snapshot per market per day;
9. updates aggregate index statistics;
10. writes `reports/ppi_daily_digest_latest.md` with price movements, evidence, proposals, stale sources and approval links;
11. records job/source status and optionally sends a compact Discord digest.

A primary and backup scheduler run are configured. Unique evidence hashes and `(market, UTC date, snapshot type)` constraints prevent duplicates. Forced retries reset job counters/source-run rows and update the same canonical snapshot.

## Production universe

`data/seed/markets.csv` contains 12 manually selected, verified political outcomes from the existing repository. It includes U.S. chamber control, major Senate/governor races and a Brazil presidential outcome. These are live market configurations, not fake prices or fair values.

Production seeding does **not** create fair values. Development demonstration values are created only with the explicit `--demo` flag and are labelled `DEMONSTRATION DATA`.

## Required values before production

No API key is required for public Polymarket market/order-book reads or deterministic classification.

Set these before publishing:

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg://...
APP_BASE_URL=https://your-domain.example
SESSION_SECRET=<long-random-secret>
PPI_ADMIN_USERNAME=admin
PPI_ADMIN_PASSWORD_HASH=<bcrypt-hash>
```

Optional:

```env
LLM_PROVIDER=deterministic        # deterministic | ollama | openai_compatible
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen3:8b
LLM_API_KEY=
DISCORD_WEBHOOK_URL=
```

Never commit `.env` or real credentials.

## Local setup

```bash
cd reality-spread
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Generate a production-grade admin hash:

```bash
PYTHONPATH=. python scripts/hash_password.py
```

Paste the output into `PPI_ADMIN_PASSWORD_HASH` in `.env`.

Initialize and seed the 12-market production universe:

```bash
PYTHONPATH=. python scripts/migrate_db.py
PYTHONPATH=. python scripts/seed_production_markets.py
```

Optional clearly labelled development values:

```bash
PYTHONPATH=. python scripts/seed_production_markets.py --demo
```

Run the first daily pipeline:

```bash
PYTHONPATH=. python scripts/run_ppi_daily.py --trigger initial
```

Start the application:

```bash
PYTHONPATH=. streamlit run app/dashboard/streamlit_app.py
```

Open `http://localhost:8501`.

## Qwen/Ollama classifier

Hermes-style evidence research can use Qwen locally without exposing keys to the browser:

```bash
ollama pull qwen3:8b
ollama serve
```

Set:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen3:8b
```

Qwen classifies and summarizes retrieved evidence. It does not see Polymarket prices during evidence classification and cannot directly publish a fair value. GDELT is disabled by default because its public endpoint can rate-limit batch research; enable it explicitly after confirming an appropriate cadence.

## Daily scheduling

Run the scheduler process separately from the web application:

```bash
PYTHONPATH=. python scripts/run_scheduler.py
```

Defaults:

- primary run: 10:00 UTC;
- backup run: 18:00 UTC.

Configure with `PRIMARY_RUN_HOUR_UTC` and `BACKUP_RUN_HOUR_UTC`.

## Docker

```bash
cp .env.example .env
# edit .env

docker compose build
docker compose run --rm web sh -c 'PYTHONPATH=. python scripts/migrate_db.py && PYTHONPATH=. python scripts/seed_production_markets.py'
docker compose up -d
```

Application: `http://localhost:8501`

The web and scheduler containers share the `ppi_data` and `ppi_reports` volumes. The complete latest digest is also preserved in durable `job_runs` metadata for hosted environments with ephemeral filesystems.

## Render production deployment

The included `render.yaml` creates:

- one PostgreSQL database;
- one Docker web service;
- two Docker cron jobs (primary and idempotent backup).

Steps:

1. Push the repository to GitHub.
2. In Render, choose **New → Blueprint** and select the repository.
3. Set `APP_BASE_URL`.
4. Generate a bcrypt hash locally and set `PPI_ADMIN_PASSWORD_HASH` in both services.
5. Optionally configure the LLM provider and Discord webhook.
6. Open a Render shell on the web service and run:

```bash
PYTHONPATH=. python scripts/migrate_db.py
PYTHONPATH=. python scripts/seed_production_markets.py
PYTHONPATH=. python scripts/run_ppi_daily.py --trigger production-initial
```

The two scheduled jobs then run automatically at 10:00 and 18:00 UTC.

## Historical market-price backfill

The official Polymarket price-history endpoint can backfill market prices:

```bash
PYTHONPATH=. python scripts/backfill_polymarket_prices.py --tracking-id RSO-0001
```

Backfilled rows are labelled `market_price_only`. The system never implies that PPI fair values existed before actual publication.

## Validation

```bash
make validate
```

This runs linting, static type checking, the full unit/integration/UI-smoke suite, and bytecode compilation. See `VALIDATION_REPORT.md` for the exact validation performed on the delivered artifact.

Equivalent commands:

```bash
ruff check app/ppi app/dashboard app/db/models.py app/db/database.py \
  scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py \
  scripts/hash_password.py scripts/seed_production_markets.py scripts/backfill_polymarket_prices.py \
  tests/test_ppi_*.py tests/test_evidence_*.py tests/test_snapshot_idempotency.py \
  tests/test_stale_data.py tests/test_polymarket_integration_mock.py \
  tests/test_publication_flow.py tests/test_streamlit_smoke.py

mypy app/ppi app/db/models.py app/db/database.py \
  scripts/run_ppi_daily.py scripts/run_scheduler.py scripts/create_admin_user.py \
  scripts/seed_production_markets.py

PYTHONPATH=. pytest -q
python -m compileall -q app scripts
```

## Documentation

- [`PPI_ARCHITECTURE.md`](PPI_ARCHITECTURE.md)
- [`PPI_METHODOLOGY.md`](PPI_METHODOLOGY.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- [`docs/ADDING_MARKETS_AND_SOURCES.md`](docs/ADDING_MARKETS_AND_SOURCES.md)
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
- [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)

## Genuine limitations

- Automated source discovery returns leads, not verified truth; important items require human review.
- The deterministic classifier is deliberately conservative and less nuanced than a strong hosted/local model.
- Polling, race-rating and comparable-market feeds vary by race and must be configured per source pack; inaccessible services are not fabricated.
- Streamlit administration is appropriate for a small editorial team, not a large multi-tenant SaaS product.
- SQLite is suitable locally. Use PostgreSQL for multiple production processes.
- Calibration conclusions require a materially larger resolved sample.
