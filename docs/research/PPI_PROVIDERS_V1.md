# PPI provider / data-acquisition layer v1

**Status:** implemented in `app/providers/`. Turns PPI Quant from "runs on seeded data" into
"acquires its own data automatically" (spec sections 5-10, 21, 31, 42, 45). Still shadow-only: the
Quant series it feeds is not the headline. No provider here ever touches a prediction-market
*price* — that separation is unchanged (`tests/test_quant_market_independence.py`).

The only manual configuration is API keys / base URLs in `.env` (see `.env.example`). With **no
keys configured** the chains degrade gracefully — seed CSVs for state lean, offline seed-file
chains for polls/candidates, and explicit `STALE` / `EMPTY` statuses — never a fabricated or
zeroed value.

---

## 1. Abstraction (`app/providers/base.py`)

`BaseProvider.fetch(session, **kwargs)` is a template method every concrete provider inherits:

1. **disabled?** (no base URL / key) → `ProviderResult(status=EMPTY)`, never an error.
2. **fresh cache?** — a `provider_cache` row for this exact request within
   `PROVIDER_CACHE_TTL_MINUTES` → served as `from_cache`.
3. **live fetch** with bounded exponential backoff (`PROVIDER_MAX_RETRIES`,
   `PROVIDER_RETRY_BACKOFF_SECONDS`; tests pass `backoff_base_seconds=0`).
4. success → normalize, validate, hash, append a `provider_cache` row (`ok=True`), update
   `provider_health` (`HEALTHY`).
5. all attempts fail → **last-known-good**: the newest `ok` `provider_cache` row for this
   `endpoint_family` → `ProviderResult(status=STALE, from_last_known_good=True)`, health `DEGRADED`.
6. no last-known-good → `ProviderResult(status=FAILED)`, `normalized_payload=None` (missing, **not**
   an empty list), health `DEGRADED`/`DOWN` after 3 consecutive failures.

`ProviderResult` carries the full spec-section-5 provenance: `provider`, `source_url`,
`retrieved_at`, `raw_payload`, `normalized_payload`, `content_hash`, `validation_status`,
`from_cache`, `from_last_known_good`, `retries`, `latency_ms`, `error`.

`ProviderChain(kind, [p1, p2, …])` runs providers in order, takes the first fresh `OK` (or a
`STALE` last-known-good if nothing fresh), and records **exactly one** `data_provider_runs` row
with `provider_requested`, `provider_used`, `fallback_reason`, `used_cache`,
`used_last_known_good`, `items_ingested`, `retries`, `sanitized_error`.

---

## 2. Providers and fallback order

| Kind | Chain (in order) | Writes |
|---|---|---|
| `election_history` | `DecisionDeskHqElectionHistoryProvider` → `WikipediaPresidentialHistoryProvider` → `SeedCsvElectionHistoryProvider` | `historical_election_results` (upsert per jurisdiction/year/office) |
| `generic_ballot` | `VoteHubGenericBallotProvider` → `DecisionDeskHqGenericBallotProvider` → `PollingSourceGenericBallotProvider` → `WebSearchGenericBallotProvider` | `national_environment_observations` (dedup on content hash) |
| `poll` | `VoteHubRacePollProvider` → `DecisionDeskHqPollProvider` → `PollingSourcePollProvider` → `WebSearchPollProvider` | `poll_observations` (dedup on content hash) |
| `candidate` | `OpenFecCandidateProvider` → `WikipediaCandidateProvider` → `SeedCandidateProvider` → `WebCandidateProvider` | `race_candidates` + `candidate_status_snapshots` (+ mirrored onto `races.dem/rep_candidate_name`) |
| `market_discovery` | `PolymarketDiscoveryProvider` (Gamma) | classification only (no forecast) |

**Verified against current docs + a live probe (2026-08):**
- **VoteHub** — base `https://api.votehub.com`, `GET /polls`, public (CC-BY-4.0, no key). `poll_type`
  is an exact-match filter: `generic-ballot` → `VoteHubGenericBallotProvider`; `us-senator` /
  `governor` → `VoteHubRacePollProvider` (state parsed from the `subject` string, e.g. "2026 North
  Carolina"; a `subject` ending " Democratic"/" Republican"/" Primary" is a primary and is
  dropped). Row shape `{id, poll_type, sample_size, population (rv/lv/a), pollster, start_date,
  end_date, answers:[{choice,pct}], seat_name, sponsors:[], internal (bool), partisan (null|"REP"|
  "DEM"), subject}`. VoteHub is the **primary** for both kinds — it is currently the only source
  returning live Senate/Governor trial heats.
- **Decision Desk HQ Polling API** — base `https://polling.decisiondeskhq.com`, `GET
  /api/v1/polls/ballot_test` (one row per candidate; fields `poll_id, question_id, pollster,
  sponsor, start_date, end_date, sample_size, population (rv/lv/all), election_type, office_type
  (Senate/Governor/House), senate_class, state, district, candidate_name, pct, cycle, source`),
  `GET /api/v1/polls/generic_ballot` (`dem_pct, rep_pct, other_pct`). Public, no auth. Both
  endpoints 200 but have been returning `[]` — kept as verification fallbacks below VoteHub.
- **Decision Desk HQ Results API v4** — base `https://resultsapi.decisiondeskhq.com`. OAuth2
  client-credentials: `POST /api/v4/oauth/token` with `{client_id, client_secret, grant_type:
  "client_credentials"}` → `{access_token, expires_in, token_type}` (cached in-process by
  client_id). `DecisionDeskHqElectionHistoryProvider` then reads `GET /api/v4/race-calls?year=Y&
  office_id=1&name=General Election&limit=250` (paginated via `total_pages`) — each row carries
  both `candidates:[{cand_id, party_id (1=D/2=R), party_name, …}]` and `topline_results.votes`
  ({cand_id: count}), so a state's D-minus-R margin is one join; district rows (ME/NE splits) are
  skipped and the national margin is the sum of the state tallies. Disabled → seed-CSV fallback
  until `DECISIONDESK_CLIENT_ID` + `DECISIONDESK_CLIENT_SECRET` (or static `DECISIONDESK_API_KEY`).
- **Wikipedia** (no key) — the primary source for **both** candidates and state lean, via the
  shared `app/providers/wikipedia.py` helpers (`fetch_wikitext_batch` does one batched
  `action=query&prop=revisions` request per group of ≤50 titles — Wikimedia's recommended pattern;
  the anonymous API 429s on rapid per-page calls — with title-normalisation / redirect resolution,
  gentle inter-chunk pacing, and generous retry/backoff).
  - `WikipediaCandidateProvider` — all of a run's race articles ("2026 United States Senate
    election in {State}" / "2026 {State} gubernatorial election") in one call; the section-0
    *Infobox election* `nomineeN` / `partyN` pairs → the D and R nominee. A stub infobox → no
    record, never a guessed name. Resolved nominees are mirrored onto
    `races.dem_candidate_name` / `rep_candidate_name`, and `ingest_political_data` rebuilds its
    `KnownRace` list from the DB after the candidate step so the poll matcher can orient
    head-to-heads whose seed config had no candidates.
  - `WikipediaPresidentialHistoryProvider` — all 51 jurisdictions × 2016/2020/2024
    ("{year} United States presidential election in {State}"), reading the per-candidate popular
    vote from the *Infobox election* (`popular_voteN` / `partyN`), falling back to the
    `{{Election box … |party= |votes= }}` results-table templates for split-elector state-years
    (Maine 2020). Per-state margins **and** the summed national margin; verified against the
    official 2016/2020/2024 national totals. Sits between DDHQ and the seed CSV, so it is the
    de-facto state-lean source whenever DDHQ credentials are absent.
- **OpenFEC** `https://api.open.fec.gov/v1` (`api_key` query param, `OPENFEC_BASE_URL` overridable)
  and **Polymarket Gamma** `https://gamma-api.polymarket.com` reuse `app/ingest/fec.py` /
  `app/ingest/polymarket_gamma.py`.

**Approximate shape, mocked tests only:** PollingSource (generic JSON, no canonical vendor).

`scripts/check_providers.py` (`make check-providers`, add `--probe` for a live request per
enabled provider) prints the whole inventory: name, endpoint family, enabled?, gating env var.

Web-search providers (`WebSearch*Provider`) take an injected extractor callable (spec section 45);
without one they yield `EMPTY`, never a guessed poll/candidate.

---

## 3. Normalization + identity

- `app/providers/normalize.py` — population map (LV/RV/A), pollster-grade bucketing (A/B/C/None),
  partisan-sponsor + internal detection (regex; **flagged, never dropped**), name normalization,
  `canonical_race_id(state, office, cycle)` → `nc-sen-2026`, and `poll_content_hash(...)` (a stable
  hash of a poll *release* so two providers surfacing the same poll collide → de-dup).
- `app/providers/race_identity.py` — `match_to_race(...)`: **deterministic** (unique
  state+office+cycle) → **fuzzy** candidate-name overlap (`difflib`, needs a clear margin over the
  runner-up) → optional **LLM resolver** hook (must return mapping + confidence + rationale +
  citations) → **abstain** below `RACE_MATCH_MIN_CONFIDENCE`. A resolver error abstains; it never
  attaches the wrong race. `resolve_candidate_party(name, dem, rep)` maps a poll answer to D/R.

---

## 4. Contamination filter (`app/providers/contamination.py`, spec section 21)

`PredictionMarketContaminationScanner.scan(text=, url=, title=)` →
`ContaminationResult(status, reason, blocked_source, hits)`:

- `BLOCKED` — the source domain is a prediction-market / betting site (Polymarket, Kalshi,
  PredictIt, Manifold, betting exchanges, `electionbettingodds.com`, `metaculus.com`, …).
- `QUARANTINED` — the body references prediction-market / betting odds ("prediction markets",
  "betting odds", "the contract is trading at …", "moneyline", …).
- `CLEAN` — usable for a blind forecast.

`SEARCH_PROMPT_PROHIBITION` is the explicit instruction text the web-search providers must include
(no market odds, no betting odds, no prediction-market probabilities).

---

## 5. Market discovery + classification (`app/providers/markets.py`, spec section 42)

`classify_market(question, description, tags)` → deterministic regex classification into
`SUPPORTED_STATEWIDE_RACE` (with a `race_hint` = `{state, office, cycle, race_id}`) /
`SUPPORTED_SENATE_CONTROL` / `SUPPORTED_HOUSE_CONTROL` (labelled supported but the Quant adapter is
UNAVAILABLE) / `UNSUPPORTED` / `AMBIGUOUS`. `classify_with_fallback(..., llm_classifier=)` calls an
LLM only for `AMBIGUOUS` / low-confidence cases; a classifier error returns `AMBIGUOUS`, never a
fabricated category. `MarketClassification.auto_publishable()` gates on
`MARKET_CLASSIFY_MIN_CONFIDENCE`; `AMBIGUOUS` markets are quarantined, not forecast.

---

## 6. Orchestration + the providers → DB → engine bridge (`app/providers/ingest.py`)

`ingest_political_data(session, race_configs, cycle=2026, …)` runs the four data chains, writes the
de-duplicated observations, records provider runs + health, and returns an `IngestSummary`
(`history_rows`, `generic_ballot_rows`, `candidate_rows`, `poll_rows`, `poll_skipped` — every
dropped poll records *why*: `unmatched_race` / `party_unresolved` / `duplicate_release`). This is
scheduler stages 3-4.

`build_quant_input_from_db(session, race_id, as_of=)` assembles a market-free
`QuantForecastInput` straight from `poll_observations` + `national_environment_observations` +
`historical_election_results` + `race_candidates` + `candidate_status_snapshots` — deriving
`candidate_mapping_confidence` from the actual poll match quality.

**Commands:**

```bash
make ingest            # live chains (graceful-degrade without keys)
make ingest-offline    # seed-file chains: no network, no keys
make ingest-dry        # offline + roll back all writes
PYTHONPATH=. python scripts/run_quant_shadow.py --from-db   # providers -> DB -> engine
```

Offline chains (`app/providers/offline.py`) read `data/seed/quant_example_races.json` (which now
carries an illustrative `generic_ballot` array) + `data/seed/historical_presidential_*.csv`.

---

## 7. Schema addition

One new table: `provider_cache` (append-only response cache + last-known-good store). The tables
the providers populate — `races`, `race_candidates`, `poll_observations`,
`national_environment_observations`, `historical_election_results`, `candidate_status_snapshots`,
`data_provider_runs`, `provider_health` — were already created by the PPI Quant v1.5 migration.
`python scripts/migrate_db.py` is idempotent and additive.

---

## 8. Still deferred

- **GPT + Claude blind-forecast runners** + the live ensemble (Phases F/G).
- **Scoring / calibration / backtesting** CLI (Phase I).
- **Frontend** + **the 10-stage scheduler** wiring these chains on a twice-daily cron (Phase H/E).
- **PollingSource** has no canonical vendor — the adapter is generic and untested against a live
  service. VoteHub + DDHQ + web fallback cover the same needs.
- **DDHQ Results API v4** state lean is wired but needs client credentials; without them
  `WikipediaPresidentialHistoryProvider` already supplies real 2016/2020/2024 state + national
  margins with no key.
