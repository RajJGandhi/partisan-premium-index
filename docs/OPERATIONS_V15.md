# Running the PPI v1.5 pipeline — Quant series only

This is the go-live checklist for the deterministic **PPI Quant** series (no LLM cost, $0). The
GPT/Claude blind benchmarks and the ensemble are opt-in (`--blind`); adding them later is a config
change, not a redeploy. Nothing here changes the public **headline** series — that stays
`legacy_blind_llm` until `docs/research/PPI_CUTOVER.md` is signed off.

---

## 1. One-time setup

1. **Merge the branch to `main`.** The site + the v1.5 workflow both key off `main`.
2. **Database.** Set `DATABASE_URL` to a persistent Postgres (the self-hosted runner already has
   one — see `docs/SELF_HOSTED_RUNNER.md`). SQLite works for a single-runner install. The v1.5
   tables are created by `python scripts/migrate_db.py` (idempotent, additive — it never touches
   the existing schema).
3. **GitHub repo variable:** `PPI_V15_ENABLED = true` (Settings → Secrets and variables →
   Actions → Variables). Without it `ppi-v15-daily.yml` is a no-op.
4. **GitHub secrets** (Actions):
   - `DATABASE_URL` — required.
   - `FEC_API_KEY` — optional; adds official FEC candidate/incumbency data for Senate races on
     top of the Wikipedia nominee lookup (free key from api.data.gov).
   - `VOTEHUB_API_KEY` — optional; VoteHub (the primary poll + generic-ballot source) needs no key.
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — **leave unset** for the Quant-only run.
5. **Nothing else.** Every data source for a Quant run is public and keyless: VoteHub (polls +
   generic ballot), Wikipedia (Senate + Governor nominees), the DDHQ polling API. Full endpoint
   list + reachability check: §4, and `python scripts/check_providers.py --probe`.

## 2. First run (manual)

```
Actions → "PPI v1.5 Pipeline (shadow)" → Run workflow
  slot: primary
  blind: false          # Quant only
```

Or on the runner directly:

```
PYTHONPATH=. python scripts/migrate_db.py
PYTHONPATH=. python scripts/run_v15_daily.py --discover --run-key "ppi-v15:$(date -u +%F):primary"
```

The pipeline: discover + bind Polymarket statewide-race contracts → snapshot their prices →
ingest polls / generic ballot / election history / candidates → run Quant per race → build the
market-free evidence bundle → (blind + ensemble skipped) → join with the market snapshot
(`market_model_spread`) → publish (append-only). One `JobRun` (`job_name = ppi-v15-daily`).

The seeded `data/seed/races_2026.json` (16 marquee races) is the fallback set if discovery finds
nothing; discovery adds/updates the rest.

## 3. Verify the run

```
PYTHONPATH=. python - <<'PY'
from app.db.database import get_session
from sqlalchemy import select, func
from app.db.models_quant import QuantForecast, Race, MarketClassification
from app.db.models import JobRun
with get_session() as s:
    job = s.execute(select(JobRun).where(JobRun.job_name=="ppi-v15-daily")
                    .order_by(JobRun.started_at.desc()).limit(1)).scalar_one()
    print("job:", job.run_key, job.status, f"{job.markets_succeeded}/{job.markets_attempted}")
    q = s.execute(select(QuantForecast.data_quality, func.count())
                  .group_by(QuantForecast.data_quality)).all()
    print("data quality:", dict(q))
    print("races bound:", s.execute(select(func.count()).select_from(Race)).scalar())
    print("quarantined contracts:",
          s.execute(select(func.count()).where(MarketClassification.status=="QUARANTINED")).scalar())
PY
```

A healthy first run looks like (16 marquee races, ~Aug 2026, no keys):

- `job.status` is `OK` or `PARTIAL`.
- ~15/16 races `OK` with a real `p_dem_win`; a race `ABSTAIN`s only when Wikipedia has no nominee
  in its infobox yet (e.g. a primary not held) **and/or** VoteHub has no general-election poll for
  it yet. Correct abstentions, not failures.
- Data quality mostly `STRONG` / `NORMAL`, with `THIN` where a race has few polls. `STRONG` needs
  ≥4 recent polls from ≥3 pollsters **and** state lean (now automatic — §4a).
- `stage 4` reports a few hundred `poll_ingest_skipped` — VoteHub also carries 2025 races and
  states outside the tracked set; those are dropped as `unmatched_race`, which is expected.

Then the twice-daily schedule (09:20 / 21:20 America/Toronto) takes over. Re-running the same slot
is idempotent (same `run_key` → no duplicate rows).

## 4. API endpoints

Every external endpoint the pipeline can call. **No key is needed for a working Quant run** that
produces real forecasts; the only entry that unlocks *more* is the DDHQ Results API (state lean,
§4a). Run `PYTHONPATH=. python scripts/check_providers.py --probe` on the runner to see, per
provider, whether it is enabled and whether its endpoint answers with the current config.

| Provider (chain) | Endpoint | Auth | Env / notes |
|---|---|---|---|
| `votehub_race_polls` (poll — **primary**) | `GET https://api.votehub.com/polls?poll_type=us-senator\|governor` | none | `VOTEHUB_API_BASE_URL`; `VOTEHUB_API_KEY` optional |
| `decisiondesk_ballot_test` (poll — fallback) | `GET https://polling.decisiondeskhq.com/api/v1/polls/ballot_test` | none | public; currently returns `[]` — kept as verification fallback |
| `pollingsource_polls` (poll — fallback) | `GET {POLLINGSOURCE_API_BASE_URL}/polls` | bearer (opt) | generic adapter, no vendor; blank ⇒ disabled |
| `votehub_generic_ballot` (generic ballot — **primary**) | `GET https://api.votehub.com/polls?poll_type=generic-ballot` | none | `VOTEHUB_API_BASE_URL` |
| `decisiondesk_generic_ballot` (generic ballot — fallback) | `GET https://polling.decisiondeskhq.com/api/v1/polls/generic_ballot` | none | public |
| `wikipedia_presidential_history` (state lean — **primary**) | `GET https://en.wikipedia.org/w/api.php` (batched, ~4 calls, cached) | none | per-state + national 2016/2020/2024 presidential margins from the *Infobox election* / `{{Election box}}` results |
| `decisiondesk_election_history` (state lean — used first if configured) | `GET https://resultsapi.decisiondeskhq.com/api/v4/race-calls` (token: `POST /api/v4/oauth/token`) | OAuth2 client-credentials | optional upgrade over Wikipedia; `DECISIONDESK_CLIENT_ID` + `_SECRET` (or static `DECISIONDESK_API_KEY`) — see §4a |
| `seed_csv_election_history` (state lean — last-resort fallback) | committed CSVs in `data/seed/` | — | national baseline populated; state file is placeholders |
| `openfec_candidates` (Senate candidates) | `GET https://api.open.fec.gov/v1/candidates/search/` | `api_key` query param | `FEC_API_KEY` (free: api.data.gov/signup); `OPENFEC_BASE_URL` overridable |
| `wikipedia_candidates` (Senate + Gov nominees — **primary**) | `GET https://en.wikipedia.org/w/api.php` (batched `action=query`, one call per run) | none | parses the *Infobox election* D/R nominees; a stub infobox → no record (never a guess) |
| `polymarket_gamma_discovery` (market discovery, `--discover`) | `GET https://gamma-api.polymarket.com/events` | none | `POLYMARKET_GAMMA_BASE_URL` |
| market snapshot prices (stage 2) | `https://clob.polymarket.com` | none | `POLYMARKET_CLOB_BASE_URL`; observation only, never a Quant input |
| GPT / Claude blind benchmarks (`--blind`) | OpenAI + Anthropic SDK defaults | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` only for a proxy/Azure |

Each chain tries its providers in order and falls through on `EMPTY`/`STALE`; a missing source is
recorded as `STALE`/`EMPTY` with a `data_provider_runs` row, never as a zero.

## 4a. State partisan lean — now automatic

`state_lean` (each state's 2016/2020/2024 Democratic-minus-Republican presidential margin) feeds
the Quant fundamentals term: `fundamental_margin = state_lean + national_environment + incumbency`,
blended with the polling margin as `μ = α·polls + (1−α)·fundamentals`. Without it, `α` is forced
to `1.0` (polling-only) and `data_quality` can't reach `STRONG`.

**`wikipedia_presidential_history` populates it automatically, no key.** It pulls every "{year}
United States presidential election in {State}" article in a few batched, cached MediaWiki calls
and reads the per-candidate popular vote from the *Infobox election* (falling back to the
`{{Election box}}` results table for split-elector years like Maine 2020); the national margin is
the sum of the state tallies. Verified against the official 2016/2020/2024 national totals.

Optional upgrades, in the order the chain tries them:

- **Decision Desk HQ Results API v4** — set `DECISIONDESK_CLIENT_ID` + `DECISIONDESK_CLIENT_SECRET`
  (request access at `decisiondeskhq.com/products`; or paste a pre-issued bearer into
  `DECISIONDESK_API_KEY`). Used ahead of Wikipedia when configured. Verify with
  `python scripts/check_providers.py --probe`.
- **Sourced CSV rows** in `data/seed/historical_presidential_state.csv`
  (`jurisdiction,year,office,dem_margin_pct,source_note`) — the last-resort fallback if both of
  the above are unavailable.

## 5. Adding the blind benchmarks later

```
pip install -r requirements-blind.txt           # on the runner
# GitHub secrets: OPENAI_API_KEY, ANTHROPIC_API_KEY
# (optional) OPENAI_BLIND_MODEL, ANTHROPIC_BLIND_MODEL  -- e.g. gpt-4.1 / claude-sonnet-5 for ~$15/mo
# Run workflow with blind: true
```

Cost, 12 markets, twice daily: ~$15/mo (non-reasoning models) to ~$60/mo (mid-reasoning + Opus).
The ensemble becomes `available` only when Quant + GPT + Claude all produce a value; otherwise it
stays `available = false` and is never reweighted.

## 6. Making Quant the public headline

Gated on the checklist in `docs/research/PPI_CUTOVER.md`. When satisfied: set
`PPI_HEADLINE_SERIES=quant` (or `ensemble`), append a dated decision to that file, redeploy. The
legacy series is retained and stays visible.
