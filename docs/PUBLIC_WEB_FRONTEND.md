# PPI static public frontend

The `web/` application is the public presentation layer for PPI. It is a static React/Vite site that consumes the sanitized bundle created by `scripts/export_public_bundle.py`.

## Architecture

```text
GitHub Actions
  -> run PPI pipeline
  -> export sanitized JSON
  -> build web/
  -> deploy web/dist to Cloudflare Pages

Public visitor
  -> static HTML, JavaScript and JSON
  -> no Python
  -> no Supabase query
  -> no LLM call
  -> no admin interface
```

## Included product surfaces

1. Homepage with headline index metrics, dislocations, index history, recent revisions and methodology summary.
2. Market directory with search, region/category/freshness/publication filters and five sort modes.
3. Market profiles with market/PPI history, component values, public thesis, evidence, revision timeline, source metadata and resolution rules.
4. Track record with preserved initial probabilities, outcomes and Brier scores.
5. Methodology with component weights, pipeline stages, safeguards, cadence and limitations.
6. System status with sanitized pipeline run history and source health.

## Responsive behavior

The UI includes layouts for desktop, tablet and mobile widths. Wide data tables remain horizontally scrollable, while the primary public information is also available through cards and page summaries.

## Build gates

```bash
cd web
npm run check
npm run build
```

The build must complete before a Cloudflare deployment is allowed.

## Data expectations

Production builds expect these generated files:

```text
web/public/data/overview.json
web/public/data/markets.json
web/public/data/track-record.json
web/public/data/system-status.json
web/public/data/markets/{slug}.json
```

The frontend handles legitimate empty states such as no published fair value, no revisions, no resolved predictions and insufficient chart history.
