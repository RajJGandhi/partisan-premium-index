# PPI public web

A static React/Vite frontend for the Partisan Premium Index.

The browser reads only sanitized JSON produced by:

```bash
PYTHONPATH=. python scripts/export_public_bundle.py
```

No Supabase credential, Python process, LLM endpoint, admin route, or public write API is present in this frontend.

## Local development

From the repository root:

```bash
make export-public
cd web
npm install
npm run dev
```

Open the Vite URL shown in the terminal.

Development mode uses bundled demonstration data only when a public JSON file is missing. Production builds never fall back to demonstration data.

## Production build

```bash
make export-public
cd web
npm ci
npm run data:check
npm run build
```

The static output is written to `web/dist`.

## Pages

- `/` — Overview and current index
- `/markets` — Searchable market directory
- `/markets/:slug` — Detailed market profile
- `/track-record` — Open and resolved prediction ledger
- `/methodology` — Components, workflow, safeguards and limitations
- `/system-status` — Sanitized pipeline and source health

## Security boundary

- All public data comes from `web/public/data`.
- `_headers` applies browser security headers and cache rules.
- `_redirects` enables client-side routes on Cloudflare Pages.
- React renders evidence and thesis text as escaped strings.
- There is no `dangerouslySetInnerHTML` usage.
