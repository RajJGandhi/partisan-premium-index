# Database connectivity

PPI is a **twice-daily batch job** that talks to a Supabase Postgres. This documents how the
connection is configured for reliability and what to do for the cloud-runner migration.

## Current target

`DATABASE_URL` (a GitHub Actions secret; never stored locally) points at the **Supabase Supavisor
session pooler**:

```
...pooler.supabase.com:5432        # session mode -- IPv4 on every tier, a dedicated
                                   # connection for the session's lifetime
```

`app/db/database.py::db_connection_mode()` classifies the URL (no credentials) as one of
`session-pooler` / `transaction-pooler` / `direct-postgres` / `sqlite`, and the engine is tuned
per mode. Source: <https://supabase.com/docs/guides/database/connecting-to-postgres>.

## The failure this hardening addresses

A real production incident: a fresh connect to the session pooler timed out
(`psycopg.OperationalError: could not receive data from server / Operation timed out`) partway
through a multi-minute run, and the whole run died. The manual retry succeeded — a connectivity
blip, not a data problem. Two root weaknesses:

1. **No `connect_timeout`** — a dead connect hung on the OS default (~2 min) instead of failing
   fast so it could be retried.
2. **No code-level retry, and a connection held open across the LLM calls** — `pool_pre_ping`
   only revalidates on *checkout*, not while a transaction sits open during ~1–2 min of external
   waits, where Supavisor / cloud NAT can reap an idle connection.

## What changed (code only — no schema, no data)

### Engine (`app/db/database.py`, tunables in `app/config.py`)

| Setting | Value | Why (twice-daily batch) |
|---|---|---|
| `connect_timeout` | `10s` | Fail a bad connect fast → retryable, instead of a ~2 min hang. |
| TCP keepalives | `idle=30s, interval=10s, count=5` | Keep a connection alive across the two LLM provider calls so it is not reaped mid-run. |
| `pool_pre_ping` | on | `SELECT 1` on checkout; a dead pooled connection is discarded and replaced. |
| `pool_recycle` | `280s` | Proactively drop a connection before the ~350s idle cull on Supavisor / NAT, so the long gap between the 09:00 and 21:00 runs never leaves a stale connection to fail on first use. |
| `pool_use_lifo` | on | Reuse the most-recently-returned connection; idle ones age out and get recycled rather than handed to the next run stale. |
| `pool_size` / `max_overflow` | `5` / `5` | The job is single-threaded; a small pool is plenty and stays well under the pooler's per-user cap. |
| `statement_timeout` | `60000ms` (server-side) | Guards a hung query. PPI's queries are tiny, so this never trips in normal operation. |

### Retry (`app/db/retry.py`)

`run_in_session(fn, description=...)` runs `fn(session)` as a committed unit of work, retried on a
**transient connectivity error only**, exponential backoff (`~1s, 2s, 4s`), hard cap
`db_retry_attempts` (default 4), then re-raise. A **fresh session per attempt** — a failed
transaction's uncommitted writes cannot be replayed on the same connection.

*Retryable* (SQLSTATE class `08`, plus `57P0x`, `53300`, `40001/40P01`, `55P03`, and driver
messages like *could not receive data* / *server closed the connection* / *connection timed out*
/ *Operation timed out*, and any `InterfaceError`). *Not retryable and raised immediately*:
`IntegrityError`, `ProgrammingError`, `28P01`/`28000` (auth), `42501` (privilege), `3D000` (bad
database), `22*` / `23*` / `42*` (data / constraint / syntax).

Used for the discrete must-not-lose writes: `job_run_lifecycle start` / `finalize` / `summary`,
the run-health computation, and `db_preflight`. The pipeline's per-market work is already
committed per market and the metadata/snapshot transaction is now committed **before** the LLM
calls (`app/ppi/pipeline.py`), so the vulnerable window is a single fast transaction.

### Preflight

`db_preflight()` — one retried `SELECT 1` at the start of `_run_daily_pipeline_locked`. A
sustained outage raises here, before any `JobRun` write; the workflow's `if: always()` finalize
step then records the run `FAILED`, so the ">18h stale" alert can fire. It is early detection,
not a guarantee the run stays connected.

### Diagnostics

`db_diagnostics()` → a sanitized dict (`connection_mode`, timeouts, `pool` status — never a URL
or credential) added to the `run_ppi_daily.py` JSON output and `job_run_lifecycle summary`.
Retries log `[db-retry] transient <type> (sqlstate=...) on attempt N/M; retrying in Xs
(connection_mode=...)`.

## Cloud-runner migration

The eventual primary pipeline runs on a **GitHub-hosted runner** (ephemeral, many transient
connections over time). Nothing here is Mac-specific — the engine reads only `DATABASE_URL`, and
`application_name=ppi-pipeline` is generic.

**Recommended:** point the `DATABASE_URL` secret at the **transaction pooler**:

```
...pooler.supabase.com:6543        # transaction mode -- a connection per transaction,
                                   # "ideal for serverless ... many transient connections"
```

The code **auto-detects `:6543`** and, for that mode only, uses `NullPool` (Supavisor owns the
pooling) and sets psycopg `prepare_threshold=None` (transaction mode does **not** support
prepared statements). **No code change is needed** — just update the secret. PPI holds no
session-scoped state (no `LISTEN/NOTIFY`, no cross-transaction `SET`, no PG advisory locks — the
pipeline lock is a filesystem lock), so it is safe in transaction mode.

Keep session mode (`:5432`) for the self-hosted `ppi-mac` runner until the cutover.
