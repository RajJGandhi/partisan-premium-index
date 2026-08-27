# PPI blind benchmarks + v1.5 ensemble (Phases F + G)

**Status:** implemented in `app/blind/`. Shadow-only. The headline series is unchanged (still the
legacy blind-LLM `raw_ppi`). Methodology version: **`ppi-blind-v1`** (benchmarks),
**`ppi-ensemble-v1.5`** (ensemble). Prompt version: **`blind_benchmark_v1`**.

Runs after the deterministic Quant forecast and its immutable, market-free `EvidenceBundle`:

```
Quant forecast ──▶ EvidenceBundle (market-free, hashed)
                      │
       ┌──────────────┼───────────────┐
       ▼              ▼               ▼
  GPT blind      Claude blind    (news worker feeds
  (spec §23)     (spec §24)       EvidenceBundle.current_news)
       └──────────────┬───────────────┘
                      ▼
        PPI Ensemble = 0.60·Quant + 0.20·GPT + 0.20·Claude   (spec §25)
                      ▼
        robustness band (needs a market prob; computed post-persistence, spec §27)
```

---

## 1. Blind forecasters (`app/blind/`)

| Module | Role |
|---|---|
| `schema.py` | `BlindForecastResponse` pydantic contract (`probability` 0-1, `should_abstain`, `rationale`, `uncertainty_drivers`, `base_rate_notes`) + tolerant `parse_blind_response` (```json fences, `<think>` preludes, trailing prose) |
| `prompt.py` | `SYSTEM_INSTRUCTIONS` (independent estimate, **no prediction-market info**, base-rate-first, honest abstention) + `build_blind_prompt(bundle, contract_question)` rendered **only** from the `EvidenceBundle`; `assert_prompt_market_free` re-scans the rendered text and calls `assert_market_free` over the bundle payload before anything is sent |
| `providers.py` | `OpenAIBlindProvider` / `AnthropicBlindProvider` (SDK imported lazily; `enabled()` False without both key + SDK; Anthropic uses `claude-opus-5` + adaptive thinking, JSON instructed in the system prompt). `DeterministicBlindProvider` — **not a fallback**: an offline plumbing stub, every row flagged `publication_status=STUB`, never in a default provider list |
| `runner.py` | `run_blind_forecasts(session, *, race_id, run_key, evidence_bundle, contract_question, providers, …)` — **no market-price / Quant-probability / other-model parameter** (asserted). One `blind_benchmark_forecasts` row per provider. Bounded retries on call/parse error → `FAILED` (probability NULL). Provider not enabled → `SKIPPED_PROVIDER` (probability NULL). **Cost control (spec §44):** a slot whose newest row is `OK` with the same evidence hash + model + prompt version is reused (no re-call); a changed evidence hash / model / prompt version, or a still-failed slot, appends a **new revision** — the prior row is never edited |
| `ensemble_runner.py` | `compute_and_persist_ensemble(...)` joins the persisted Quant forecast + the two blind rows via `app.quant.ensemble.combine_ensemble` (predeclared `0.60/0.20/0.20`). Any missing / abstained / failed component → `available = False`, present components **never reweighted**. Robustness needs a `market_probability` and is computed here, strictly after persistence. Append-only per `(race_id, run_key, methodology_version)` with a revision bump on change |
| `web_evidence.py` | `collect_race_news(...)` — bounded contamination-filtered web search via an **injected** `search_fn` (OpenAI/Anthropic web search). Every doc runs through `PredictionMarketContaminationScanner`; `BLOCKED`/`QUARANTINED` docs are stored (`race_news_items`) but excluded from `EvidenceBundle.current_news`. No `search_fn` → `[]` (news is optional; v1 Quant never uses it) |

## 2. Schema (`app/db/models_quant.py`, additive)

- **`blind_benchmark_forecasts`** — race-centric, append-only per
  `(race_id, run_key, provider, methodology_version, revision)`. Columns: probability,
  should_abstain, rationale, uncertainty_drivers_json, status
  (`OK`/`ABSTAINED`/`FAILED`/`SKIPPED_PROVIDER`), model_name/model_version, prompt_version/hash,
  evidence_bundle_hash, prompt/completion/total tokens, web_search_calls, raw_request/raw_response,
  FLAG-only `reviewed_*`, `integrity_flag`, `correction_of_id`. Entirely separate from the legacy
  market-centric `llm_forecasts` (unchanged).
- **`race_news_items`** — append-only, dedup on content hash; stores
  `contamination_status` / `contamination_reason` / `blocked_source`.
- `ensemble_forecasts.openai_forecast_id` / `anthropic_forecast_id` FKs now point at
  `blind_benchmark_forecasts`; added `integrity_flag` / `integrity_note`.

`python scripts/migrate_db.py` is idempotent and additive.

## 3. Running it

```bash
pip install -r requirements-blind.txt        # only needed for the REAL GPT/Claude calls
# .env: OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENAI_BLIND_MODEL, ANTHROPIC_BLIND_MODEL

make quant-shadow-blind                       # Quant -> bundle -> GPT/Claude -> ensemble (live)
PYTHONPATH=. python scripts/run_quant_shadow.py --blind-stub   # offline plumbing (STUB rows)
```

With no keys: blind rows are `SKIPPED_PROVIDER` (probability NULL) and the ensemble row is
`available = False` — **never** reweighted to Quant alone.

## 4. Separation guarantees (`tests/test_blind_market_independence.py`)

- `run_blind_forecasts` signature has no `market*` / `price` / `bid` / `ask` / `spread` /
  `polymarket` / `quant_probability` / `ensemble` parameter; it takes `evidence_bundle` +
  `contract_question` and nothing model-comparative.
- Nothing under `app/blind/` imports `app.ppi.polymarket` / `app.ingest.polymarket_*` / kalshi /
  predictit / `MarketSnapshot` (static AST test).
- The rendered prompt contains no market/betting term except the single "you are not given the
  market price … do not guess the market price" reminder; `build_blind_prompt` raises if the
  bundle carries any forbidden key.
- GPT never sees Claude's forecast or the Quant probability, and vice versa — each provider gets
  only `(SYSTEM_INSTRUCTIONS, user_prompt)`.

## 5. Robustness (spec §27, `app/quant/ensemble.py` — already tested)

`dispersion = pstdev(Q, GPT, Claude)`; `max_pairwise_disagreement`; band **HIGH** (|market −
ensemble| ≥ 10pt AND max pairwise ≤ 8pt) / **MEDIUM** (≤ 15pt) / **LOW** (the gap is mostly PPI's
own models disagreeing). `null` in shadow because no market probability is joined.

## 6. Deferred

- **Scoring / calibration / backtesting** (Phase I) — `forecast_scores` table exists; the runner
  does not. Brier per series (market / quant / openai / anthropic / ensemble / legacy_llm) at
  standard horizons.
- **Frontend** model-breakdown + robustness display (Phase H); the **10-stage scheduler** wiring
  Quant→bundle→blind→ensemble on a twice-daily cron and then flipping the headline (Phase E).
- **Live-verified** OpenAI/Anthropic calls — the SDK calls are written to the documented shapes
  (`claude-opus-5` + adaptive thinking; OpenAI `chat.completions` + `response_format` json_object)
  but exercised only via fakes + the deterministic stub in the default test suite.
- **Performance-weighted ensemble** (spec §26) — architecture only; not activated.
- **Web-evidence `search_fn`** — the worker + contamination filter + storage are built; the actual
  OpenAI/Anthropic web-search callable is injected by the (future) scheduler.
