# Prospective Matched-Model Comparison: Qwen3-8B (V1) vs. DeepSeek V4 Flash 0731

**This is a preregistration of a live, matched, model-comparison experiment. It is a separate
document from, and does not replace, alter, or extend, `docs/research/PPI_V2_PREREGISTRATION.md`
(the Qwen V1-vs-V2 *prompt* preregistration, which as of this writing remains an uncommitted
working draft in the local working tree — not referenced further here except to note that its
axis of variation, prompt structure, is explicitly orthogonal to and out of scope for this
document; see Section 12.)**

## 0. Document status

| Field | Value |
|---|---|
| Status | **Preregistered — no eligible live matched observation exists yet.** This document is written to be sufficient, on its own, to later demonstrate that this experiment's design, hypotheses, and evaluation criteria were fixed before any live matched DeepSeek-vs-Qwen observation was generated. |
| Preregistration timestamp (UTC) | `2026-08-14T02:14:58Z` |
| Repository | `RajJGandhi/partisan-premium-index` |
| Commit SHA used as methodological baseline | `b2add4480a9d7db7b7c9e347631c74e23d69483a` (`main`) — the merge commit of PR #12, itself immediately following PR #11's merge (`0fb6b5323ac012d3c4df6c057b0bb5f126b885ba`) |
| Author / project | Partisan Premium Index (PPI) research engine |
| This document | `docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md` |

### Inspection note — a real discrepancy found while verifying this document against actual code

Section 1 of the task that produced this document required verifying every parameter from the
repository rather than from memory of prior conversation turns. That verification surfaced a
material fact worth recording here explicitly, since it affects what "the current DeepSeek
integration" actually means as of the baseline commit above:

During earlier work in this repository's history, two additional commits (`3b6892a`, "feat: add
Arm G (DeepSeek explicit thinking-mode reasoning audit)", and `d8120fc`, "fix: raise Arm G's token
budget and timeout after a live run truncated reasoning") were pushed to the
`feat/openrouter-deepseek-provider` branch **after** PR #11 (which introduced that branch) had
already been merged into `main` (merge timestamp `2026-08-12 21:47:28 -0400`; those two commits
are timestamped `22:17:41` and `22:51:50` the same evening). As a result, **those two commits, and
everything in them, are not present in `main` as of the baseline commit above** — they exist only
on the (now-stale-relative-to-main) `feat/openrouter-deepseek-provider` branch. Concretely, this
means `main`'s current `openrouter_provider_config()` in `app/ppi/blind_forecast.py` has **no
`reasoning`/`max_output_tokens`/`timeout` override parameters at all** — it unconditionally
returns `reasoning={"enabled": False}` — and `_call_openrouter()` does not capture a
`reasoning_trace`/`reasoning_details` field. The "Arm G" reasoning-audit diagnostic (Section 11 of
this document's disclosure list) was real, ran successfully, and its results are committed at
`data/shadow_experiments/deepseek_reasoning_audit_20260812.json` — but the *code* that produced it
is not part of `main`, only its *output* is.

**This does not block or compromise this preregistration.** The primary matched arm this document
specifies (Section 4) requires `reasoning.enabled = false` — which is the *only* behavior `main`'s
current, actually-merged `openrouter_provider_config()` supports; there is no override to
accidentally misuse. It is recorded here purely for accuracy, per the instruction to "preserve the
actual V1 configuration and explicitly note the discrepancy" wherever this document's assumptions
and the repository's actual state might otherwise be conflated. No code was modified to produce
this note or this document.

---

## 1. Experiment start rule (binding)

This preregistration becomes effective, and the confirmatory sample begins, only after **all** of
the following have occurred, in order:

1. This document is committed to a feature branch.
2. That commit is merged into the default branch (`main`) — **by Raj, not by Claude**, per this
   task's explicit instruction. No PR produced by this task may be self-merged.
3. A **separate, subsequent** implementation change — the "dual-series production
   implementation" — is designed, reviewed, and merged. That implementation does not exist yet.
   At minimum it must: call `generate_blind_forecast` a second time per market per canonical
   cycle with an explicit DeepSeek `ProviderConfig` (reusing `openrouter_provider_config()` as it
   exists on `main` at the time, i.e. `reasoning={"enabled": False}` unless a future document
   amends this preregistration first); ensure both calls run from one single evidence
   collection/classification pass per market per cycle (Section 5); and record the resulting
   `JobRun`/cycle as the first eligible one only once wired into the actual scheduled pipeline
   (`.github/workflows/ppi-daily.yml`, currently unmodified by this document).
4. The first scheduled (`primary` or `backup` trigger) matched run occurs after (1)–(3).

No diagnostic, shadow, reasoning-audit, or historical DeepSeek forecast generated before all four
conditions are met may be retroactively counted toward the preregistered sample — see Section 11
for the explicit list of what is excluded.

The first eligible `JobRun.id` and its `run_key`/timestamp must be recorded, at the time it
occurs, in an append-only experiment metadata record (a new file, e.g.
`data/experiments/qwen_vs_deepseek_v1_metadata.json` or an equivalent durable, version-controlled
location — the exact mechanism is an implementation detail of step 3 above, not fixed by this
document, but it must be append-only and must not be edited retroactively once the first entry is
written).

This document is **not** amended after seeing eligible live results. If circumstances require a
change during or after the collection window, the change must be a separately committed,
timestamped, clearly-labeled, prospective-only amendment (Section 9's "no mid-experiment tuning"
rule governs what counts as a change requiring this).

---

## 2. Research question

Holding the evidence packet, forecast question, prompt, blindness controls, parsing rules, and
observation time constant, does a substantially stronger hosted language model produce
meaningfully different — and ultimately better — political probability forecasts than the
current local Qwen3-8B baseline?

This is deliberately split into two separate questions, evaluated on different timelines and with
different evidentiary standards:

### Near-term behavioral question (available immediately, every cycle)
Does DeepSeek produce more discriminating / probabilistically resolved forecasts than Qwen3-8B —
i.e., does it avoid Qwen's observed tendency to collapse many different markets onto the same
handful of round-number probabilities?

### Ultimate forecasting question (available only once markets resolve)
Does DeepSeek produce more accurate and better-calibrated forecasts, as measured against actual
election outcomes, once they occur?

**Stated explicitly and held throughout this document: greater probability dispersion is not
itself evidence of greater accuracy.** A model that discriminates more between markets but is
worse-calibrated, or simply wrong more often, is not an improvement. The near-term behavioral
question can be answered on the preregistered 60-cycle schedule; the ultimate forecasting question
may remain open long after that window closes, because the underlying 2026 elections have not yet
occurred.

---

## 3. Frozen model configuration

### 3.1 Baseline — Qwen3-8B (V1), verified from `app/ppi/blind_forecast.py` and `app/config.py` at the baseline commit

| Parameter | Value | Source |
|---|---|---|
| Provider | `ollama` | `app/config.py:28` (`llm_provider`, production env sets this to `ollama`; see `.github/workflows/ppi-daily.yml:67`) |
| Model | `qwen3:8b` | `app/config.py:30,36` (`llm_model`/`ollama_model`); `.github/workflows/ppi-daily.yml:68` |
| `model_provider` persisted | `"ollama"` | `LLMForecast.model_provider`, set from `ProviderConfig.provider` in `default_provider_config()` |
| Methodology version | Not yet a persisted field (`methodology_version` does not exist as a column on `LLMForecast` as of this baseline — see the uncommitted `PPI_V2_PREREGISTRATION.md` draft, which proposes adding it for an unrelated, orthogonal purpose). This document refers to the current, unversioned production behavior simply as "Qwen V1" / "the current production Qwen configuration." | `app/db/models.py` `LLMForecast` column list |
| Prompt version | `fair_value_v0.1` | `PROMPT_VERSION`, `app/ppi/blind_forecast.py:33` |
| Prompt text | `SYSTEM_INSTRUCTIONS` + `USER_PROMPT_TEMPLATE`, quoted verbatim in Section 4 below | `app/ppi/blind_forecast.py` |
| Temperature | `0.15` | `GENERATION_TEMPERATURE`, `app/ppi/blind_forecast.py:37` |
| Context length | `4096` | `GENERATION_NUM_CTX`, `app/ppi/blind_forecast.py:38` |
| Max output tokens | Not set in the Ollama request payload — falls through to Ollama's own model default (no explicit cap in `_call_ollama`) | `_call_ollama`, `app/ppi/blind_forecast.py` |
| `top_p`/`top_k`/`seed` | Not set — Ollama/model defaults | `_call_ollama` `options` dict |
| Reasoning / thinking | **Not requested.** The `think` field is omitted entirely from the Ollama API payload (confirmed empirically earlier in this repository's history: this fully suppresses `<think>`-tag output on the production Ollama build) | `_call_ollama`, `app/ppi/blind_forecast.py` |
| Retry policy | `MAX_RETRIES = 2` → up to 3 total attempts (1 initial + 2 retries), each re-calling the model and re-attempting JSON extraction/schema validation on failure | `app/ppi/blind_forecast.py:36`, `generate_blind_forecast`'s `for attempt in range(1, MAX_RETRIES + 2)` loop |
| Parser | `_extract_json_object` — brace-matching JSON extraction with `<think>`-tag stripping fallback; validated against the `BlindFairValueEstimate` Pydantic schema | `app/ppi/blind_forecast.py` |
| Output schema | `BlindFairValueEstimate`: `fair_value` (required, 0–1), `confidence` (required, 0–1), `should_abstain` (default `False`), `rationale_short` (required, ≤700 chars), `key_uncertainties` (list, ≤5 items), `base_rate_notes` (default `""`, ≤500 chars) | `app/ppi/blind_forecast.py` |
| Twice-daily cadence | 09:00 / 21:00 America/Toronto, native per-entry GitHub Actions `timezone:` schedule | `.github/workflows/ppi-daily.yml:33-37` |
| Uniqueness key | `(market_id, run_slot, model_provider)` — widened from the historical `(market_id, run_slot)` by PR #11 specifically so a second provider's row can never overwrite or be skipped in favor of Qwen's | `app/db/models.py`, `LLMForecast.__table_args__` |
| Canonical/headline status | Sole member of `PRIMARY_SERIES_PROVIDERS = {"ollama"}` — the only provider whose forecasts drive `compute_run_classification`'s canonical/contaminated determination and the `BlindIndexRun` aggregate | `app/ppi/blind_forecast.py`, `app/ppi/run_classification.py` |

**This experiment does not alter any of the above.** No change is made to Qwen's temperature,
context size, prompt, retry policy, parser, schedule, or canonical status to make the comparison
easier, harder, or more convenient in any direction.

### 3.2 Strong-model arm — DeepSeek V4 Flash 0731, verified from `app/ppi/blind_forecast.py` and `app/config.py` at the baseline commit

| Parameter | Value | Source |
|---|---|---|
| Provider | `openrouter` | `openrouter_provider_config()`, `app/ppi/blind_forecast.py` |
| Pinned model (requested, as sent on the wire) | `deepseek/deepseek-v4-flash-0731` | `app/config.py:44` (`openrouter_model`) |
| Canonical OpenRouter slug (informational only, never substituted) | `deepseek/deepseek-v4-flash-20260731` (checked live via `GET /api/v1/models`, 2026-08-12 — see `docs/research/OPENROUTER_INTEGRATION.md`) | External, not repo-tracked |
| API base URL | `https://openrouter.ai/api/v1` | `app/config.py:42` |
| `model_provider` persisted | `"openrouter"` | `LLMForecast.model_provider` |
| Prompt version | `fair_value_v0.1` — **identical to Qwen's**, per Section 4 below. Not the `fair_value_v0.2_decomposed`/decomposition prompt from the separate, still-uncommitted V1-vs-V2 work. | `USER_PROMPT_TEMPLATE`/`build_prompt`, reused verbatim |
| Temperature | `0.15` | `ProviderConfig.temperature` defaults to `GENERATION_TEMPERATURE`; `openrouter_provider_config()` does not override it, so it inherits the same numeric value as Qwen |
| Max output tokens | `2048` | `app/config.py:46` (`openrouter_max_output_tokens`), passed as `max_tokens` in the OpenRouter request body |
| Context length (provider ceiling, informational) | 1,048,576 tokens; provider `max_completion_tokens` ceiling 384,000 | `GET /api/v1/models`, checked live 2026-08-12 |
| `response_format` | `{"type": "json_object"}` — always set, unconditionally, in the current merged `_call_openrouter()` | `app/ppi/blind_forecast.py` |
| Reasoning / thinking | **`reasoning = {"enabled": False}`, hardcoded, unconditional.** As of the baseline commit, `openrouter_provider_config()` accepts no override — this is the only value it can produce. See the inspection note in Section 0: this matches exactly what Section 4 of the source task requires, so no gap exists between "what the design calls for" and "what the code can currently do." | `app/ppi/blind_forecast.py` |
| Retry policy | Identical `MAX_RETRIES = 2` / 3-total-attempts loop, shared code path with Qwen | `generate_blind_forecast` |
| Timeout | `90` seconds | `app/config.py:45` (`openrouter_timeout_seconds`) |
| Attribution headers | `HTTP-Referer: https://partisan-premium-index.pages.dev`, `X-OpenRouter-Title: Partisan Premium Index` (non-secret, OpenRouter's own documented leaderboard-attribution mechanism) | `app/ppi/blind_forecast.py` |
| Served-model / usage metadata | `served_model` (from the response body's own `model` field), `prompt_tokens`, `completion_tokens`, `total_tokens` — captured in `_call_openrouter`'s returned `usage_info` dict and merged into `generation_params_json` | `app/ppi/blind_forecast.py` |
| Cost | Not computed inline by production code as of this baseline; OpenRouter's listed per-token pricing ($0.08/1M prompt, $0.18/1M completion, checked live) is applied post hoc for reporting, exactly as `scripts/run_shadow_experiment.py`'s `_estimate_openrouter_cost` already does for diagnostics (that function exists only on the un-merged `feat/openrouter-deepseek-provider` branch per the Section 0 inspection note — the dual-series production implementation, Section 1 step 3, will need its own equivalent, or reuse that one once merged) | `scripts/run_shadow_experiment.py` (not yet in `main`) |

**Never used**: `deepseek-v4-flash-latest` or any other alias; OpenRouter auto-routing; any
fallback model; any provider/model substitution on failure. `openrouter_model` is a literal
pinned string with no alias resolution anywhere in the call path.

---

## 4. Exact prompt text (frozen, identical for both arms in this experiment)

```
SYSTEM_INSTRUCTIONS = """You are a calibrated election and prediction-market research assistant.

Your job is to estimate the fair probability that a specific option-level event contract resolves YES.

You are NOT seeing current market prices. Do not infer or invent market odds.

Think like a disciplined forecaster:
- Use base rates.
- Separate evidence from speculation.
- Be conservative under uncertainty.
- Avoid overreacting to narrative.
- If evidence is thin, lower confidence.
- If the market is extremely niche or evidence is insufficient, you may abstain.

Return ONLY valid JSON. No markdown. No commentary outside JSON.
"""

USER_PROMPT_TEMPLATE = """Estimate the blind fair value for this option-level event contract.

CONTRACT:
- Market question: {question}
- Resolution criteria: {resolution_criteria}
- Category: {category}
- Region: {region}
- End date: {end_date}

IMPORTANT BLINDNESS RULE:
You are not given the market price, bid, ask, spread, volume, or order-book data. Do not guess the market price. Estimate your own fair probability only.

EVIDENCE PACKET:
{evidence_text}

OUTPUT JSON SCHEMA:
{{
  "fair_value": number between 0 and 1,
  "confidence": number between 0 and 1,
  "should_abstain": boolean,
  "rationale_short": string, max 500 characters,
  "key_uncertainties": array of 1 to 5 short strings,
  "base_rate_notes": string, max 300 characters
}}

Calibration guidance:
- 0.50 means true tossup.
- 0.10 means unlikely but plausible.
- 0.01 means very unlikely but not impossible.
- 0.90 means very likely but not certain.
- Avoid 0 or 1 unless resolution is already certain.
- If you abstain, still provide your best fair_value, but set confidence low.
"""
```

Qwen's full request is `SYSTEM_INSTRUCTIONS + "\n\n" + USER_PROMPT_TEMPLATE.format(...)` sent as a
single `prompt` field to Ollama's `/api/generate`. DeepSeek's full request is the same two strings
sent as separate `system`/`user` chat messages to OpenRouter's `/chat/completions` — semantically
identical content, delivered in the shape each API expects. This is the **only** structurally
necessary difference between the two arms' requests; no wording, instruction, or schema field
differs.

### 5. Critical experimental choice: isolating the model as the sole variable

DeepSeek uses the same substantive V1 forecast prompt as Qwen (Section 4) — not the
decomposition/V2 prompt. DeepSeek reasoning is explicitly `enabled = false`, matching Qwen's own
"no thinking requested" configuration (Qwen has no `think` field at all; DeepSeek's closest
equivalent state is `reasoning.enabled = false`, which is what `main`'s code already does
unconditionally per Section 3.2).

This avoids bundling three separate, confounding changes — model, prompt, and reasoning mode —
into one treatment. If DeepSeek's forecasts differ from Qwen's under this design, the only
frozen, deliberately-varied factor is **which model produced them**.

Two related but explicitly out-of-scope diagnostics exist and must not be confused with this
design:

- **Arm G (max-effort reasoning-enabled audit)**: a separate, noncanonical, forensic diagnostic
  that intentionally *did* change the reasoning setting (to `enabled=true, exclude=false,
  effort="max"`) specifically to audit *how* DeepSeek reasons, not to compare its accuracy against
  Qwen under matched conditions. It remains a diagnostic; it is not, and does not become, part of
  this preregistered live series.
- **The one-shot Arm F DeepSeek diagnostic** (12 markets, 1 repetition each, against the
  `job_run_id=21` frozen evidence packet): pre-experiment motivating evidence only (Section 11).
  It used the same V1 prompt and `reasoning=false` configuration this document freezes, which is
  exactly *why* it is informative as motivating evidence — but it was not generated under this
  preregistration's start rule (Section 1) and cannot count toward the confirmatory sample.

---

## 6. Evidence matching (mandatory, unconditional)

For every eligible market/cycle, once the dual-series production implementation (Section 1, step
3) exists:

1. Evidence is collected **once** per market per cycle, exactly as today's single-series pipeline
   already does (`app/ppi/pipeline.py`'s evidence-collection step, unchanged).
2. The existing evidence classification/deduplication pipeline runs exactly as it does today
   (per-`(market_id, content_hash)` dedup, `require_live_classifier=True` under
   `strict_llm_only`) — unchanged.
3. `build_blind_evidence_packet(session, market, require_live_classifier=True)` is called and its
   result is the frozen packet for that market/cycle.
4. The packet is hashed. `generate_blind_forecast` already computes `prompt_hash =
   _stable_hash(build_prompt(packet))` per forecast row (`app/ppi/blind_forecast.py`); the dual-
   series implementation must ensure both providers' calls are made from the **same** underlying
   `EvidenceItem` rows with **no evidence-discovery step in between** (the same sequencing
   guarantee already relied on within a single canonical run today), so both providers' prompt
   hashes are directly comparable and any divergence is detectable.
5. That exact same frozen packet — byte-identical evidence content, in the same order — is passed
   to both Qwen and DeepSeek. Neither model independently retrieves evidence; there is no
   mechanism in the current codebase for a model to retrieve evidence itself, and the dual-series
   implementation must not introduce one.
6. Both forecasts are recorded as separate, independent `LLMForecast` rows
   (`(market_id, run_slot, model_provider)` uniqueness, Section 3.1), neither overwriting the
   other.
7. Only after both rows are persisted may market-price/benchmark data be joined for analysis
   (`join_forecast_with_price`, unchanged, called independently per row — this already the
   existing, structural blindness guarantee: price joining happens strictly after persistence,
   never before, for every existing forecast row today).

### Matched-observation validity requires equality of

- Question text (`market.question`, identical `Market` row referenced by both calls).
- Evidence item content (title, summary, source, published_at, category — the exact fields
  `build_blind_evidence_packet` includes).
- Evidence item ordering — **already deterministically transformed** by existing methodology:
  `build_blind_evidence_packet`'s query orders by `EvidenceItem.published_at.is_(None),
  EvidenceItem.published_at.desc()` (most-recent-first, NULLs last), a stable, reproducible
  ordering given the same underlying rows. No new ordering rule is introduced by this document.
- Evidence timestamp cutoff — both calls read from the database at (functionally) the same
  instant, within the same cycle, before either model is called.
- Evidence packet hash — Qwen's and DeepSeek's `prompt_hash` values must correspond to
  byte-identical evidence content (the prompt *text* differs trivially in delivery shape per
  Section 4, but the evidence substring within each must match).
- Blindness-filter result — `assert_blind_packet(packet)` must pass identically for both, since
  both are constructed from the same packet dict before any provider-specific call.

**A matched observation is not valid if the evidence hashes diverge.** Section 13 specifies how an
invalid/unmatched pair is classified and handled — it is excluded from paired analysis, not
silently dropped or silently forced to match.

---

## 7. Blindness (frozen, unchanged from current production policy)

Neither model may see, before committing its forecast:

- Polymarket implied probability, bid, ask, spread, liquidity, volume.
- Any previous PPI value or previous market price.
- Prediction-market consensus of any kind.
- Any benchmark numeric forecast designated benchmark-only (none currently exist in the evidence
  pipeline; if DDHQ, Silver Bulletin, Race to the White House, or similar numerical forecasters
  are incorporated later as benchmarks, **their numerical forecasts must not enter either model's
  primary evidence packet unless a future preregistration explicitly changes this rule** — this
  document does not authorize that change).
- The other model's forecast (each `generate_blind_forecast` call is independent; nothing in the
  current or planned code path passes one model's output to the other).
- Future evidence (evidence collection happens once, before either call, per Section 6).
- Outcome information unavailable at the observation timestamp.

`assert_blind_packet` / `FORBIDDEN_PACKET_KEYS` (`app/ppi/blind_forecast.py`) enforce this
mechanically today, identically for both providers, since both consume the same packet-
construction code path before any provider branch. This document does not add, remove, or loosen
any forbidden key.

**Current expert qualitative race ratings**: may only be included in evidence if already permitted
by the frozen evidence-collection methodology as it exists today (i.e., if such content already
legally enters the evidence pipeline as a news/RSS source today, it continues to; no new source
category is being added by this document). This document does not introduce any new evidence
source.

---

## 8. Sample and cadence

**60 matched canonical cycles**, at the existing twice-daily scheduled cadence: 09:00 and 21:00
America/Toronto (native GitHub Actions per-entry `timezone:` scheduling,
`.github/workflows/ppi-daily.yml`).

Use the **actual execution/observation timestamp** (`generated_at`, set from `now` at the moment
`generate_blind_forecast` runs) as the cycle's timestamp of record, not the nominal
scheduled-cron time, when a run starts late.

At the currently-observed 12 tracked markets (`markets_attempted: 12`, confirmed directly from
live `JobRun` data — `job_run_id=21` and `job_run_id=22` both processed exactly 12 markets), the
planned maximum is approximately:

```
60 cycles × 12 markets = 720 matched market-cycle pairs
```

**This is a planned ceiling, not a promise of 720 independent observations.** The inferential unit
and missing-pair handling matter more than the raw row count:

- Repeated forecasts for the *same, still-unresolved* race across successive cycles are
  correlated, not independent — the same underlying electoral reality is being re-observed, with
  partially overlapping evidence, 120 times across the window (60 cycles × 2 observations of that
  one race). They are not 120 independent samples of "will this race go Democratic," and this
  document does not treat them as such (Section 15).
- A missed or unmatched cycle (Section 13) reduces the realized count below 720; the window is
  defined by **60 actually-matched cycles**, not by 30 calendar days, so a missed cycle extends
  wall-clock time to reach 60 rather than silently shrinking the sample.
- The market universe itself may change during the window (Section 10), which can change the
  per-cycle market count without changing the cycle count.

---

## 9. Primary near-term behavioral endpoint

### H1 (primary, confirmatory)

> DeepSeek has a lower share of exact-`0.45` forecasts than Qwen on matched observations, over the
> preregistered 60-cycle window.

This is a **directional**, matched comparison — not anchored to any specific magnitude from prior
diagnostics. **Share of forecasts exactly equal to `0.45`** is the single formally designated
primary clustering endpoint, chosen because that specific failure mode (Qwen's tendency to
collapse many different markets onto exactly `0.45`) was identified prospectively, before this
experiment, from real canonical production data (`job_run_id=21`) — not chosen after the fact to
flatter a convenient threshold.

**Secondary, descriptive behavioral metrics** (reported alongside H1, none independently
confirmatory):

- Share of forecasts on the 0.05 probability grid.
- Number of distinct probability values used across the matched-cycle set.
- Within-cycle probability standard deviation / dispersion across the 12-market universe.

### H2 (secondary) — probability resolution

> DeepSeek produces greater probability differentiation than Qwen on matched observations
> (operationalized via the secondary metrics above).

H2 is explicitly secondary to H1; a pass on H2 without a pass on H1 is not sufficient to claim the
clustering failure mode has been addressed, since H2's metrics can move for reasons unrelated to
escaping the specific `0.45` anchor (e.g., shifting to a different, equally narrow cluster).

---

## 10. Accuracy endpoints (ultimate forecasting question)

### H3 (primary accuracy hypothesis, resolution-dependent)

> Once outcomes resolve, DeepSeek achieves a lower average Brier score than Qwen on eligible
> matched forecasts.

**Primary metrics** (both require resolved outcomes, evaluated on whatever subset of the 12
tracked markets resolves during or after the collection window):

- Brier score, paired per market/resolution, since both forecasts share market, timestamp
  (cycle), and evidence packet.
- Log loss, paired identically.

**Secondary metrics:**

- Calibration (predicted-probability vs. observed-frequency binning), to the extent sample size
  allows — most of the 12 tracked markets resolve around the November 2026 midterms or later, so
  this may remain thin.
- Mean absolute probability error where a meaningful reference exists.
- Model-vs-market score differential (each model's score against Polymarket's own implied
  probability, joined only after both forecasts are persisted per Section 6).
- Performance broken down by race/category/horizon, only where sample size permits — not
  performed post hoc merely because a break-down looks favorable to one model.

**Explicitly stated: DeepSeek is not declared superior merely because it is more decisive.** H1/H2
answer "did DeepSeek break the clustering pattern"; H3 answers "is DeepSeek actually a better
forecaster." Neither substitutes for the other. **The accuracy verdict (H3) may remain unresolved
for a long time after the 60-cycle behavioral window closes**, because the underlying elections
have not yet occurred as of this preregistration.

### H4 (exploratory/secondary) — calibration

> DeepSeek is better calibrated than Qwen on matched, resolved forecasts.

Treated as exploratory/secondary unless the eventually-resolved sample size is large enough to
support a meaningful calibration assessment (no specific minimum count is fixed in advance, for
the same reason the original Qwen V1-vs-V2 preregistration declined to fix one: election
resolution dates are not fully known at preregistration time).

---

## 11. Previous diagnostics — explicitly excluded from the confirmatory sample

Model selection for this experiment was informed by pre-experiment diagnostics. This is disclosed
honestly rather than presenting DeepSeek as having been chosen blindly:

1. **`job_run_id=21`** — the original canonical production run in which Qwen V1 exhibited severe
   `0.45`/0.05-grid clustering (9 of 12 forecasts at exactly `0.45`). Real canonical historical
   data; motivating evidence for *why this experiment exists at all*; not a DeepSeek observation.
2. **The 4-arm (later extended to 6-arm) noncanonical shadow experiment**
   (`data/shadow_experiments/shadow_45pct_clustering_20260811*.json`) — characterized Qwen's
   clustering behavior under various prompt/sampling perturbations. No DeepSeek involvement.
3. **The one-shot 12-market DeepSeek diagnostic** (Arm F, `reasoning=false`, the same V1 prompt
   this document freezes) — produced substantially more differentiated probabilities than Qwen on
   the same frozen `job_run_id=21` evidence packet (0/12 at exactly `0.45`, vs. Qwen's 9/12).
   Directly motivating for this preregistration's H1, but pre-dates the start rule (Section 1) and
   is explicitly **not** part of the confirmatory sample.
4. **The Arm G max-reasoning-effort audit** (`reasoning=true, effort="max"`) — a separate
   forensic/qualitative diagnostic of DeepSeek's reasoning process, published as
   `data/shadow_experiments/deepseek_reasoning_audit_20260812.json` and an accompanying report.
   Used a *different* reasoning configuration than this document freezes; excluded both for that
   reason and because it pre-dates the start rule.
5. **The GA/NC/MI Senate "resolution diagnostic"** (Arms A/D/E, `job_run_id=22` evidence,
   temperature/prompt-structure comparisons) — Qwen-only and prompt-structure-focused; not a
   DeepSeek-vs-Qwen comparison and not part of this document's motivating evidence either, noted
   here only for completeness of "what diagnostics exist in this repository's history."

**None of the above count toward the preregistered 60-cycle sample.** They are prior evidence that
motivated running this experiment, not part of it.

---

## 12. Explicit separation from the Qwen V1-vs-V2 prompt experiment

This experiment asks: **Qwen3-8B V1 vs. DeepSeek V4 Flash 0731, using the same V1 methodology.**

It does **not** answer: **V1 prompt vs. decomposition/V2 prompt.**

The existing (still-uncommitted, per repeated explicit instruction across this repository's
history) Qwen V1-vs-V2 prompt preregistration (`docs/research/PPI_V2_PREREGISTRATION.md`) remains
a wholly separate, orthogonal research question — model identity is frozen there (both V1 and V2
use `qwen3:8b`/`ollama`), while prompt structure is frozen here (both arms use the identical V1
prompt). This document does not combine datasets with that work, does not change DeepSeek to the
V2/decomposition prompt during this experiment, and does not treat any V2-prompt observation
(Qwen or DeepSeek) as part of this confirmatory sample.

A future **factorial** design — model × prompt, i.e. Qwen-V1, Qwen-V2, DeepSeek-V1, DeepSeek-V2 —
could in principle test both axes at once. That design is out of scope for both existing
preregistrations and would require its own document if pursued.

---

## 13. Reliability, failures, and unmatched pairs

Tracked separately, per provider, per cycle:

- Successful forecasts (`status = "OK"`).
- Explicit abstentions (`status = "ABSTAINED"`, `should_abstain = true`).
- Malformed responses (JSON extraction or schema-validation failure after exhausting retries →
  `status = "FAILED"`, `error_message` starting `ValidationError:`/`FAILED_PARSE:`).
- API errors (`error_message` starting `HTTPError:`).
- Timeouts (`error_message` starting `Timeout:`).
- Token exhaustion (a `FAILED` row whose `completion_tokens` sits at or near
  `openrouter_max_output_tokens`, indicating truncation rather than a genuine parse failure —
  distinguished descriptively, not automatically, since `main`'s current `generation_params_json`
  records `completion_tokens` via `usage_info` but has no dedicated "truncated" flag).
- Provider failures (`error_message` starting `MissingAPIKey:`/`RateLimited:`).
- Parsing failures (subset of malformed responses above).

**A DeepSeek failure may never be replaced by a Qwen value, and a Qwen failure may never be
replaced by a DeepSeek value.** No silent fallback exists in `generate_blind_forecast` for either
provider today (`AUTOMATED_PROVIDERS` gating, explicit `SKIPPED_PROVIDER`/`FAILED` states, no
cross-provider substitution path anywhere in the function) — this document requires the dual-
series implementation preserve that property exactly.

**For any paired analysis requiring both forecasts** (H1, H2, H3, H4), a market-cycle is
classified **unmatched** if either model lacks a valid terminal `OK`/`ABSTAINED` forecast for that
exact `(market_id, run_slot)`. Unmatched cycles are excluded from paired analysis, not imputed,
not backfilled, and not silently dropped without being counted and reported. The failed attempt
and its recorded reason are preserved exactly as persisted (or, for the failing side, as far as
`generation_params_json`/`error_message` capture it) — never deleted or overwritten.

**Do not rerun solely to manufacture a complete matched pair**, unless the existing canonical
retry policy (`MAX_RETRIES = 2`, applied identically and automatically within the same cycle by
both providers' own call paths) already permits the exact same retry prospectively for both
series — which it already does, since the retry loop is shared code, not something invoked
separately per analysis need.

---

## 14. Cost and latency (secondary operational metrics)

Persisted where available, per DeepSeek forecast (via `generation_params_json`'s `usage` field, as
`_call_openrouter` already populates for `prompt_tokens`/`completion_tokens`/`total_tokens`/
`served_model`/`reasoning`):

- Prompt/input tokens.
- Completion/output tokens.
- Reasoning tokens: not applicable to this experiment's primary arm, since `reasoning.enabled =
  false` means no reasoning tokens are generated or billed separately.
- Total tokens.
- Estimated API cost, computed post hoc from OpenRouter's listed per-token pricing (Section 3.2) —
  never computed by the model itself, never fed back into `fair_value`/`confidence`/`status`.
- Model latency and error/retry count, to the extent the dual-series implementation logs
  per-request timing (not currently a persisted field on `LLMForecast`; if added, it must be
  purely descriptive metadata, following the same principle as cost).

**DeepSeek API cost must not affect the forecast itself** — this is already structurally true
(nothing in `generate_blind_forecast` reads cost/usage before computing `fair_value`), and this
document requires it remain true.

Total experimental API cost is to be reported at the end of the collection window (or periodically
during it) as a practical, secondary result — cost/performance being a genuinely interesting
question independent of the accuracy verdict.

---

## 15. No mid-experiment tuning

During the 60-cycle collection window, the following are **frozen**:

- Model snapshots (`qwen3:8b`; `deepseek/deepseek-v4-flash-0731`, pinned, never an alias).
- Provider designation (`ollama`; `openrouter`).
- The V1 prompt (Section 4), for both arms.
- Reasoning setting (`think` omitted for Qwen; `reasoning={"enabled": False}` for DeepSeek).
- Temperature (`0.15` for both).
- Token/context limits (Qwen: `num_ctx=4096`, no explicit output cap; DeepSeek:
  `max_output_tokens=2048`).
- Blindness logic (`assert_blind_packet`/`FORBIDDEN_PACKET_KEYS`).
- Evidence pipeline (collection, classification, dedup, `require_live_classifier` filter,
  ordering).
- Retry policy (`MAX_RETRIES=2`, shared code path).
- Parser (`_extract_json_object`).
- Market-question semantics (the frozen `Market.question`/`rules` text per tracked market).
- Scheduled cadence (09:00/21:00 America/Toronto).

**A forecast looking wrong, surprising, or "stupid" is not grounds for changing any of the
above mid-window.**

If a genuine safety/correctness bug forces a change (e.g. a blindness leak, a parser bug producing
invalid data, a discovered secret-exposure risk):

- All prior data is preserved — nothing is deleted or edited.
- The break is recorded explicitly (a dated note, at minimum in this document's amendment trail
  per Section 1, and ideally in a linked deviation log matching the pattern the Qwen V1-vs-V2
  preregistration already establishes for its own deviations policy).
- Pre- and post-change observations are never silently pooled into one dataset.
- The response is either (a) restart the preregistered 60-cycle sample from the fix forward, or
  (b) treat the change as the start of a new, separately versioned methodology (per this
  project's existing rule that "any model change starts a clearly versioned series and requires a
  documented comparison") — decided and recorded explicitly when the situation arises, not
  pre-decided here in the abstract.

---

## 16. Market-universe changes during the window

Specified prospectively:

- **A market resolves**: it stops producing new matched cycles going forward but its already-
  collected matched cycles remain part of the sample (used for H1/H2 throughout; eligible for
  H3/H4 once resolution data is available).
- **Polymarket removes a market**: same treatment as resolution for forecast-generation purposes;
  price-joining for that market simply has no further data after removal, but the blind-forecast
  side is unaffected since forecasts never depend on price data existing.
- **A candidate withdraws / question semantics become invalid**: the market is excluded from
  further matched-cycle collection from that point forward (both series stop being asked an
  invalidated question); prior valid cycles remain in the sample.
- **A race is cancelled or postponed**: same treatment — no further cycles for that market;
  existing ones remain.
- **A new market is added to PPI's tracked universe** after the experiment starts: it **may** be
  collected going forward (both providers see it under the same frozen configuration), but it does
  **not** silently expand the preregistered confirmatory universe. Because this document does not
  define a prospective inclusion rule for new markets beyond "the same frozen configuration
  applies to it," any analysis of a market added after the start of collection must be reported
  **separately** from the confirmatory analysis of the originally-tracked 12 markets, unless a
  future amendment to this document explicitly defines an inclusion rule.

**No market is added to or removed from the confirmatory analysis after seeing model
performance.** Universe changes are driven only by the real-world events above, never by which
market makes one model look better or worse.

---

## 17. Publication and status rules

- **Qwen V1 remains the existing headline/canonical PPI series** throughout the comparison window,
  unless separately changed by an explicit, future methodology decision. This document does not
  change public-site headline methodology.
- **DeepSeek forecasts may be stored, and optionally displayed publicly**, but only under a
  clearly, distinctly labeled **`experimental` / strong-model-comparison** surface — structurally
  separated so it can never be confused with, or substitute for, the headline PPI series. This
  mirrors the existing automatic-publish-once-persisted rule already governing Qwen
  (`.claude/rules/research-integrity.md`: "a canonical forecast publishes automatically once
  persisted; data-integrity review may only flag it") — extended to DeepSeek's separate surface,
  not loosened for it. No selective human approval exists for either series; a human reviewer may
  only flag a suspected data-integrity problem, on either series, never approve or edit a value.
- DeepSeek must not, under any circumstance, enter the headline PPI index, the
  `raw_ppi`/standardized aggregate views, or replace Qwen in any public surface, during this
  experiment.
- This document does not implement any of the above publication surfaces — that remains part of
  the dual-series production implementation (Section 1, step 3) and any accompanying public-site
  work, both out of scope for this preregistration itself.

---

## 18. Decision rules after 60 cycles

At the end of the 60 matched cycles, the following behavioral conclusions are possible — none is
predetermined:

- **A.** DeepSeek clearly reduces the previously observed clustering failure (H1 passes with a
  clear, consistent margin).
- **B.** DeepSeek changes probability resolution but not conclusively (H1/H2 show a small, mixed,
  or inconsistent effect).
- **C.** Qwen and DeepSeek behave similarly (no material H1/H2 difference).
- **D.** The comparison is compromised by reliability or methodology issues (e.g. a high unmatched
  rate, a discovered blindness leak, a discovered evidence-mismatch pattern) severe enough that
  the collected data cannot support a clean behavioral conclusion.

**DeepSeek is not adopted as canonical merely because outcome A occurs.** Canonical adoption
requires a **separate, subsequent methodology decision** — not automatically triggered by this
document — that considers, at minimum:

- Resolved-outcome accuracy (H3/H4), when available.
- Reliability (Section 13's failure-rate tracking).
- Calibration.
- Cost (Section 14).
- Reproducibility.
- Interpretability and alignment with this project's broader research goals.

**If resolved-outcome data are not yet available when the 60-cycle window closes** (plausible,
since several tracked contracts resolve around or after the November 2026 midterms), the correct,
honest status to report is: **the behavioral experiment (H1/H2) is complete; the accuracy
experiment (H3/H4) remains pending resolution.** This is not treated as a failure of the
experiment design — it is the expected shape of comparing forecasters against events that have not
yet happened.

---

## 19. Statistical caution

No inferential-statistics sophistication is claimed here beyond what the data structure actually
supports. Repeated forecasts for the same race across time are correlated — the 60 × 12
observations are **not** 720 independent samples of 720 different elections; they are repeated,
correlated re-observations of (at most) 12 distinct electoral outcomes, taken at up to 60 points
in time each.

Primary reporting for this experiment is:

- Matched descriptive comparisons (paired differences per cycle, per market).
- The clustering metrics defined in Section 9, computed across the full matched-cycle set.
- Within-market trajectories over time (how each model's forecast for a given race evolves cycle
  to cycle) — descriptive, not treated as independent draws.
- Eventual outcome-level scoring (H3/H4) — the only point at which the *outcome* dimension (12
  distinct, eventually-independent electoral results) provides genuinely independent information,
  and even there, standard errors/uncertainty statements must account for the small number of
  distinct races (at most 12), not the larger number of forecast-cycles.

**If formal inferential statistics (confidence intervals, significance tests, etc.) are used later
to summarize this experiment's results, they must explicitly account for repeated observations /
clustering by market**, and the specific method used must be documented separately from this
preregistration (a future analysis document, not a retroactive edit to this one).

---

## 20. Preregistration integrity checklist

Verified directly against repository state at the baseline commit before this document was
written or committed:

- [x] No eligible DeepSeek live *matched* run has occurred — `.github/workflows/ppi-daily.yml`
      sets only `LLM_PROVIDER: ollama` / `LLM_MODEL: qwen3:8b`; no `OPENROUTER_API_KEY` reference
      exists in that workflow; no code path currently calls `generate_blind_forecast` with an
      OpenRouter `ProviderConfig` from within the scheduled pipeline.
- [x] Previous diagnostics are clearly separated and listed (Section 11) — none retroactively
      included.
- [x] Exact model slug pinned (`deepseek/deepseek-v4-flash-0731`) — verified against
      `app/config.py:44` on `main`, not memory.
- [x] Exact Qwen configuration copied from repository truth (Section 3.1), including the
      discovery that `max_output_tokens` is genuinely unset for Qwen (an asymmetry with
      DeepSeek's explicit 2048 cap, recorded honestly rather than assumed symmetric).
- [x] Exact prompt text included verbatim (Section 4), copied directly from
      `app/ppi/blind_forecast.py` at the baseline commit.
- [x] Reasoning state explicit for both arms, and verified to match what the actually-merged code
      can produce (Section 0's inspection note) — not merely what was intended.
- [x] 60-cycle stopping rule stated unambiguously (Section 8), including the "cycles, not
      calendar days" framing and its rationale.
- [x] No Polymarket leakage possible by design — `assert_blind_packet` runs identically for both
      providers, before any provider-specific branch, on the same shared packet-construction code
      path (Section 7).
- [x] Failures and unmatched pairs have prospective treatment (Section 13), with no silent
      fallback in either direction.
- [x] No silent model fallback exists in `generate_blind_forecast` for either provider (verified
      directly in the function body, not assumed).
- [x] No historical mutation — this document proposes no change to any existing `LLMForecast` row,
      `JobRun`, or evidence data.
- [x] No methodology change was accidentally implemented — this task modified no `.py` file; only
      this markdown document (and, transitively, nothing else) is part of the corresponding
      commit.

---

*End of preregistration. No production code was modified, no forecast was generated, no dual-
series production implementation was written, and no prior canonical or diagnostic data was
altered in the creation of this document.*
