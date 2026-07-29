# PPI public data export

The public website must never query the production database directly. Instead,
`scripts/export_public_bundle.py` reads the canonical database during a trusted
GitHub Actions run and writes a sanitized, static JSON bundle to
`web/public/data`.

## Run it

```bash
PYTHONPATH=. python scripts/export_public_bundle.py
```

Use another destination during testing:

```bash
PYTHONPATH=. python scripts/export_public_bundle.py \
  --output-dir /tmp/ppi-public-data
```

The script uses the existing `DATABASE_URL` configuration. On production runs,
that is the Supabase session-pooler URL stored in GitHub Actions secrets.

## Files

- `overview.json`: headline coverage, current index values, largest premiums,
  latest run, recent publications, and index history.
- `markets.json`: compact public directory of all enabled markets.
- `track-record.json`: open and resolved prediction ledger plus aggregate Brier
  scoring.
- `system-status.json`: sanitized run and source health data.
- `markets/{slug}.json`: complete public market profile, including snapshots,
  components, approved revisions, accepted evidence, public sources, and
  resolution data.
- `manifest.json`: schema version and generated-file inventory.

## Privacy boundary

The exporter intentionally excludes:

- database credentials and application secrets;
- admin users or reviewer identities;
- raw market/API payloads;
- full article text;
- classifier raw output and reasoning;
- source queries and adapter configuration;
- rejected or pending-review evidence;
- pending fair-value proposals;
- internal notes and approval justification;
- raw or sanitized stack traces.

Only evidence with `relevant = true` and a review state of `APPROVED` or
`AUTO_ACCEPTED` can enter the bundle. Source URLs are restricted to public HTTP
or HTTPS destinations; localhost, private-network, credential-bearing, and
fragment-only URLs are removed.

## Reliability

The output directory is generated in a temporary sibling directory and swapped
into place only after all files serialize successfully. This prevents a failed
run from leaving a partially updated public bundle and removes obsolete market
files when the tracked universe changes.

The bundle schema is currently `1.0`. Frontend changes should treat that value
as the contract version.
