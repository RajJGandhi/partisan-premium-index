# Adding Markets and Source Packs

## Through the admin console

1. Open **Administration → Markets**.
2. Add the Gamma market ID, slug, question and YES/NO CLOB token IDs.
3. Enable the market.
4. Add source-pack JSON with aliases and preferred sources.
5. Confirm the five weights total 100%.
6. Open **Administration → Sources** and add source adapters.
7. Open **Administration → Components** and enter sourced component values.
8. Run the daily pipeline.
9. Review and approve the initial fair-value proposal.

## Source-pack example

```json
{
  "queries": [
    "Maine Senate election 2026 poll",
    "Maine Senate race 2026 candidates"
  ],
  "aliases": [
    "Maine Senate",
    "ME Senate"
  ],
  "resolution_criteria": "Resolves from certified election results.",
  "polling_sources": [
    "official pollster feed"
  ],
  "forecast_sources": [
    "race-rating source"
  ],
  "comparable_market_sources": [],
  "official_sources": [
    "maine.gov"
  ],
  "preferred_domains": [
    "reuters.com",
    "apnews.com",
    "maine.gov"
  ]
}
```

## Adapter configuration

### Google News / RSS search

```text
source_type: google_news
query: Maine Senate election 2026
config_json: {"max_items": 10}
```

### Direct RSS/Atom feed

```text
source_type: rss
url: https://example.com/feed.xml
allowed_domains_json: ["example.com"]
config_json: {"max_items": 20}
```

### GDELT

```text
source_type: gdelt
query: "Maine Senate" election
config_json: {"max_items": 10}
```

### JSON/API source

```json
{
  "items_path": "data.polls",
  "title_field": "name",
  "url_field": "source_url",
  "date_field": "published_at",
  "content_field": "summary",
  "max_items": 20
}
```

The source URL must pass SSRF validation. Add a narrow domain allowlist wherever possible.

### Manual external market observation

Use **Administration → Evidence** and label the observation as a manual external-market input. Do not claim an automated integration when no reliable public API exists.

## CLI production seed

Edit `data/seed/markets.csv` and `data/seed/source_packs.json`, then run:

```bash
PYTHONPATH=. python scripts/seed_production_markets.py
```

The script upserts by `tracking_id` and does not create fake production fair values.

## Allowed domains and safe fetching

For RSS or JSON sources, configure a comma-separated domain allowlist in Administration → Sources. PPI rejects private/internal addresses, embedded URL credentials and non-HTTPS schemes by default, and revalidates every redirect target to reduce SSRF risk.
