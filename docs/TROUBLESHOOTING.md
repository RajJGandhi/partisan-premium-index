# Troubleshooting and Recovery

## `ModuleNotFoundError: app`

Run commands from the repository root with:

```bash
PYTHONPATH=. python scripts/migrate_db.py
```

## Mamba shell warning

A `libmamba Shell not initialized` message is unrelated to PPI when the active Python virtual environment still executes commands. Either initialize mamba:

```bash
eval "$(mamba shell hook --shell zsh)"
```

or use the project `.venv` directly.

## Polymarket API failures

- Confirm DNS and outbound HTTPS access.
- Confirm the Gamma ID/slug and CLOB token IDs.
- Inspect Administration → Failures.
- Inspect `raw_market_responses` for status/error metadata.
- Retry with:

```bash
PYTHONPATH=. python scripts/run_ppi_daily.py --trigger retry --force
```

Public market/order-book reads require no trading credentials.

## Google News or GDELT failures

Source failures are isolated. The market snapshot can still be written as `PARTIAL` if pricing succeeded.

- Disable GDELT with `GDELT_ENABLED=false` if rate-limited.
- Reduce source count or add a direct RSS/API source.
- Preserve previous evidence when no reliable new source is available.

## LLM unavailable or malformed output

The app still boots and the daily job continues. Evidence classification falls back to deterministic rules. Check `classifier_provider` and `reason` on the evidence item.

## Duplicate evidence or snapshots

The database enforces:

- unique `(market_id, content_hash)` evidence;
- unique `(market_id, snapshot_date, snapshot_kind)` snapshots.

A forced rerun updates the existing daily row.

## Weight validation error

The five market weights must total exactly `1.0` within numerical tolerance. Missing component values do not remove their configured original weights; the calculation records effective weights separately.

## Admin login failure

Generate a fresh hash:

```bash
PYTHONPATH=. python scripts/hash_password.py
```

Copy the complete output into `PPI_ADMIN_PASSWORD_HASH`. Restart the web service after changing environment variables.

## SQLite lock errors

The repository enables WAL mode and a 30-second busy timeout. For multiple production processes, move to PostgreSQL instead of sharing SQLite over a network filesystem.

## Correcting a published fair value

Never update a `fair_value_revisions` row. Create a new proposal/revision and explain the correction. The schema includes `correction_of_revision_id` for explicit correction relationships.
