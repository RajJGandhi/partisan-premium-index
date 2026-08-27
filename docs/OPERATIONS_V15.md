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
   - `FEC_API_KEY` — recommended; enables OpenFEC candidate/incumbency lookups for Senate races
     (free key from api.data.gov). Without it, candidates come from market discovery + the web
     fallback only.
   - `VOTEHUB_API_KEY` — optional; VoteHub generic-ballot is tried without a key first.
   - `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — **leave unset** for the Quant-only run.
5. **Nothing else.** The Decision Desk HQ polling API (race polls + generic ballot) is public.

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

A healthy first run looks like:

- `job.status` is `OK` or `PARTIAL` (a `PARTIAL` with a few `ERROR` races is fine — check
  `summary.races[*].error`; usually "no ingested data for race" for a race with no polls yet).
- Data quality is a **spread** of `NORMAL` / `THIN` / `DEGRADED`, with `STRONG` only where a race
  has ≥4 recent polls from ≥3 pollsters **and** state-lean data (see §4). Mostly `THIN` this far
  from the election is expected and correct.
- No mass `ABSTAIN` — an abstain means candidate-mapping confidence < 0.60 or literally no polls
  and no fundamentals for that race.

Then the twice-daily schedule (09:20 / 21:20 America/Toronto) takes over. Re-running the same slot
is idempotent (same `run_key` → no duplicate rows).

## 4. State partisan lean — the main quality lever (optional)

`historical_presidential_state.csv` ships with **no real states** (only XX/YY/ZZ placeholders), so
`state_lean` is `None` for real races and the forecast is **polling-only** (α = 1.0). That is a
correct degraded mode — well-polled races still get a real forecast — but it caps data quality
below `STRONG` and leaves thinly-polled races at `THIN`/`ABSTAIN`.

To enable fundamentals, do **one** of:

- **(a) Wire the Decision Desk HQ results API.** Set `DECISIONDESK_RESULTS_BASE_URL`
  (+ `DECISIONDESK_API_KEY` if required) and confirm the endpoint/shape in
  `app/providers/election_history.py::DecisionDeskHqElectionHistoryProvider._do_fetch` against
  current DDHQ docs (`results-api-docs.decisiondeskhq.com`). The provider then populates
  `historical_election_results` automatically each run.
- **(b) Add sourced rows to the CSV.** One row per state per year (2016 / 2020 / 2024),
  `jurisdiction,year,office,dem_margin_pct,source_note`, `dem_margin_pct = Dem% − Rep%` (D+5 →
  `5`, R+5 → `-5`). Cite an official canvass or an established aggregator. The national baseline
  (`historical_presidential_national.csv`) is already populated.

`data_quality` will move from `THIN`/`NORMAL` toward `STRONG` as coverage improves — visible per
race on `/v15` and in `forecast_scores` once races resolve.

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
