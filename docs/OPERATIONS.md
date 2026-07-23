# PPI Operations

## Daily commands

```bash
source .venv/bin/activate
cd /path/to/reality-spread
PYTHONPATH=. python scripts/run_ppi_daily.py --trigger manual
```

Review:

```bash
sqlite3 data/reality_spread.db \
  "select run_key,status,markets_succeeded,markets_attempted,error_count from job_runs order by id desc limit 5;"
```

Review the durable digest first:

```bash
cat reports/ppi_daily_digest_latest.md
```

Then open:

- System status for failures/freshness;
- Administration → Evidence;
- Administration → Approval queue.

## Primary and backup scheduler

```bash
PYTHONPATH=. python scripts/run_scheduler.py
```

The primary and backup runs have different job keys but share evidence and daily-snapshot uniqueness rules. A backup run can fill missing data without creating duplicate canonical snapshots.

## Daily editorial procedure

1. Confirm the latest job is `OK` or understand every `PARTIAL` result.
2. Review evidence marked `PENDING`.
3. Reject false positives and flag uncertain sources for follow-up.
4. Inspect proposed fair-value changes and their component provenance.
5. Approve/edit/reject with written justification.
6. Confirm the public market detail and revision history.
7. Never modify published history directly in the database.

## Backups

SQLite:

```bash
mkdir -p backups
sqlite3 data/reality_spread.db ".backup backups/ppi-$(date -u +%F-%H%M).db"
```

PostgreSQL:

```bash
pg_dump "$DATABASE_URL" > "backups/ppi-$(date -u +%F-%H%M).sql"
```

Back up before migrations and before any manual data repair.

## Recovery

A failed daily job can be retried from the admin console or CLI:

```bash
PYTHONPATH=. python scripts/run_ppi_daily.py --trigger retry --force
```

Evidence hashes and canonical daily snapshots make the retry idempotent. A forced retry also resets the existing job counters and replaces its source-run diagnostics instead of accumulating duplicate operational counts.

If a job remains `RUNNING` after process termination, a forced rerun resets that job key and continues. Completed market rows from earlier incremental commits remain preserved.

## Secrets

Never print or paste:

- `DATABASE_URL` with credentials;
- `PPI_ADMIN_PASSWORD_HASH`;
- `LLM_API_KEY`;
- `DISCORD_WEBHOOK_URL`;
- `.env` contents.

The public system-status page only exposes sanitized errors.
