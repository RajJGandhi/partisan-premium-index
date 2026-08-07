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

## Primary blind-LLM (Qwen) forecast series

```bash
sqlite3 data/reality_spread.db \
  "select run_slot,status,fair_value,confidence,raw_ppi from llm_forecasts order by id desc limit 20;"
```

`status` is one of `OK`, `ABSTAINED`, `FAILED`, or `SKIPPED_PROVIDER`. In production today, expect `SKIPPED_PROVIDER` on every row: the scheduled GitHub Actions workflow runs on a hosted runner with `LLM_PROVIDER=deterministic` because it cannot reach a local Ollama instance (see `PPI_ARCHITECTURE.md` → "Production automation constraint"). To generate real forecasts, run the pipeline from a machine with Ollama running and `LLM_PROVIDER=ollama` set:

```bash
ollama pull qwen3:8b
ollama serve &
LLM_PROVIDER=ollama PYTHONPATH=. python scripts/run_ppi_daily.py --trigger manual
```

A row already at `OK` is never overwritten by a later run of the same twice-daily slot; only `FAILED`/`SKIPPED_PROVIDER` rows are retried in place. To correct a genuinely wrong forecast, do not edit the row — that slot's history is immutable by design.

The Streamlit app's **LLM Forecasts** page (public, in the main navigation) shows the full history with market/date/status filters, a historical probability chart with a derived confidence band, and a CSV export button. **Administration → LLM Forecasts** lets a signed-in admin mark a forecast `APPROVED_FOR_PUBLICATION` or `FLAGGED` with a note — this never edits the forecast's numeric value, only the separate `reviewed_status`/`reviewed_by`/`reviewed_at`/`review_notes` columns.

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
