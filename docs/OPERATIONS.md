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

`status` is one of `OK`, `ABSTAINED`, `FAILED`, or `SKIPPED_PROVIDER`. In production, the scheduled workflow runs on the self-hosted Mac runner with `LLM_PROVIDER=ollama` (see `docs/SELF_HOSTED_RUNNER.md`), so expect real `OK`/`ABSTAINED` rows on every canonical run. `SKIPPED_PROVIDER` only appears from a run where `LLM_PROVIDER` wasn't a live provider (e.g. a local run without Ollama configured). To generate real forecasts locally, run the pipeline from a machine with Ollama running and `LLM_PROVIDER=ollama` set:

```bash
ollama pull qwen3:8b
ollama serve &
LLM_PROVIDER=ollama PYTHONPATH=. python scripts/run_ppi_daily.py --trigger manual
```

A row already at `OK` is never overwritten by a later run of the same twice-daily slot; only `FAILED`/`SKIPPED_PROVIDER` rows are retried in place. To correct a genuinely wrong forecast, do not edit the row — that slot's history is immutable by design.

The Streamlit app's **LLM Forecasts** page (public, in the main navigation) shows the full history with market/date/status filters, a historical probability chart with a derived confidence band, and a CSV export button. A canonical forecast (`OK` or `ABSTAINED`, from a run classified `canonical`) publishes to the sanitized public export automatically — see `app/ppi/public_forecast.py` — there is no approval step. **Administration → LLM Forecasts** lets a signed-in admin mark a forecast `FLAGGED` (removing it from public display for a genuine data-integrity concern) or reset it back to `UNREVIEWED`, with a note — this never edits the forecast's numeric value, only the separate `reviewed_status`/`reviewed_by`/`reviewed_at`/`review_notes` columns, and it is never used to selectively approve a forecast based on its contents.

### Read-only forecast run audit

`DATABASE_URL` only exists inside GitHub Actions secrets — a raw model response, the exact
evidence packet, retry count, or generation parameters for a specific `JobRun` can't be inspected
from a local machine without production DB access. `scripts/audit_llm_forecast_run.py` (and the
`PPI Forecast Run Audit` workflow, `.github/workflows/ppi-audit.yml`) exist specifically for
this: a strictly SELECT-only diagnostic that never writes to the database, never runs a
migration, the forecasting pipeline, the public exporter, or a Cloudflare deploy, and never
supersedes the run it audits (see `tests/test_audit_llm_forecast_run.py` for the enforced
no-writes contract).

```bash
# From GitHub: Actions -> "PPI Forecast Run Audit (read-only)" -> Run workflow -> job_run_id
gh workflow run ppi-audit.yml -f job_run_id=21

# Locally, with DATABASE_URL already set in your environment:
PYTHONPATH=. python scripts/audit_llm_forecast_run.py --job-run-id 21 --output audit-report.json
```

The full sanitized report (per-forecast raw responses, evidence titles/sources/timestamps,
generation parameters, and cross-market evidence-overlap/duplicate-packet analysis) is uploaded
as a workflow artifact; only aggregate counts (no raw text) are written to the job's step
summary. Each forecast's `evidence_items` also carries the exact `summary` text and `category`
shown to the model, and each forecast row carries `market_category`/`market_region`/
`market_end_date`/`market_resolution_criteria` -- enough to exactly reconstruct the blind evidence
packet a historical run used, for a shadow experiment (below).

### Shadow experiments

For characterizing an anomaly in an already-completed run (e.g. an unexpected probability
distribution) without touching the canonical series at all:

1. Run the audit workflow for the run in question (above) and download its artifact --
   `audit-report.json`.
2. `scripts/run_shadow_experiment.py --frozen-inputs audit-report.json --output results.json`
   replays each market's *exact* frozen evidence packet (same question, same evidence, same
   `assert_blind_packet` blindness check as production) under one or more experimental "arms" --
   different prompts and/or generation settings -- calling a local Ollama instance directly. It
   never touches `DATABASE_URL`, never creates a `JobRun`/`LLMForecast` row, never calls
   `app.ppi.pipeline`/`generate_blind_forecast`, and writes only to the local `--output` file, so
   these generations can never be confused with, publish as, or supersede a real canonical
   observation.
3. `scripts/analyze_shadow_experiment.py --results results.json --frozen-inputs audit-report.json --original-cluster-slugs <slug1,slug2,...>`
   computes per-arm/per-market statistics (mean, stdev, unique-value count, within-/between-market
   variance, evidence-count and evidence-overlap correlations, and whether the original clustering
   reproduces).

Shadow experiment results are diagnostic data, not part of the PPI history -- store them under
`data/shadow_experiments/` if you want them retained, never in a location the public exporter or
Streamlit review UI reads from.

## Primary and backup scheduler

The canonical production schedule is 09:00 and 21:00 America/Toronto, run by `.github/workflows/ppi-daily.yml` on the self-hosted runner (see `docs/SELF_HOSTED_RUNNER.md`) — that workflow's own `schedule:` trigger is what actually drives production, not the script below.

```bash
PYTHONPATH=. python scripts/run_scheduler.py
```

`scripts/run_scheduler.py` is a separate, manual/local alternative (a long-running `BlockingScheduler` process) for running primary/backup on a machine without the GitHub Actions runner set up — not the production mechanism. The primary and backup runs have different job keys but share evidence and daily-snapshot uniqueness rules. A backup run can fill missing data without creating duplicate canonical snapshots.

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
