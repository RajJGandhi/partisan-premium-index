# PPI Methodology V2 Preregistration

**Status:** Preregistered before any live V2 production observation. No V2 forecast has been
generated in production as of this document. This document is written to be sufficient, on its
own, to later demonstrate that V2's design, hypotheses, and evaluation criteria were fixed before
any live V2 result was observed.

## 14. Preregistration metadata

*(Presented first, deliberately, so every downstream section can be read against a fixed
provenance record.)*

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Repository | `RajJGandhi/partisan-premium-index` |
| Commit SHA at preregistration | `a7eff636d7a089f1bd209abcfbbbc0918148ee97` (`main`) |
| Author / project | Partisan Premium Index (PPI) research engine |
| Status | Preregistered — no live V2 production observation exists yet |
| Forensic audit reference | `job_run_id=21`, `run_key=ppi-daily:2026-08-11:primary`; produced via `scripts/audit_llm_forecast_run.py` (`.github/workflows/ppi-audit.yml`) |
| Shadow experiment reference | `data/shadow_experiments/shadow_45pct_clustering_20260811.json` (240 raw generations), `..._frozen_inputs.json` (exact replayed evidence packets), `..._analysis.json` (computed statistics), produced via `scripts/run_shadow_experiment.py` / `scripts/analyze_shadow_experiment.py` |
| This document | `docs/research/PPI_V2_PREREGISTRATION.md` |

---

## 1. Motivation

### The observed V1 anomaly

`job_run_id=21` (`run_key=ppi-daily:2026-08-11:primary`, classified `canonical`, the first
canonical run of the strict-blind-LLM series) produced the following, read directly from the
audit report (`data/shadow_experiments/shadow_45pct_clustering_20260811_frozen_inputs.json`,
itself the enriched output of `scripts/audit_llm_forecast_run.py` against that run):

- **9 of 12 forecasts landed at exactly `fair_value = 0.45`.**
- **Only 4 unique probability values across 12 markets**: `{0.45, 0.50, 0.55, 0.65}`.
- **12 of 12 raw model responses were unique** (no duplicated raw text) — the clustering is not a
  storage or caching artifact; the model produced distinct text every time, and 9 of those 12
  distinct texts independently contained the literal value `0.45`.
- **No parser/storage/default/fallback explanation was found.** `BlindFairValueEstimate.fair_value`
  has no default (`Field(ge=0.0, le=1.0)`, required); `generate_blind_forecast` never clips,
  rounds, or coerces `fair_value`; every one of the 12 forecasts had `retries = 0` (succeeded on
  the first attempt); the one `0.45` literal found elsewhere in the codebase
  (`app/ppi/classifier.py`'s `DeterministicClassifier`, capping an unrelated evidence-relevance
  score) is provably unreachable in this run, since `LLM_PROVIDER=ollama` routes to
  `OllamaClassifier`, never `DeterministicClassifier`.
- **Thinner and more overlapping evidence among the clustered forecasts**: the 9 clustered markets
  had an average of **3.78** evidence items (individual counts `[4, 2, 6, 5, 5, 4, 2, 1, 5]`); the
  3 escaped markets averaged **5.00** (`[6, 4, 5]`). Eight distinct articles were each shared
  across 2–6 of the 12 markets' evidence packets (by `content_hash`), predominantly among the
  clustered set.
- **Forensic classification: mixed cause** — genuine Qwen probability clustering (the raw model
  output literally contains `0.45`, not a post-hoc artifact) compounded by evidence-quality
  effects (thinner, more generic, more cross-market-duplicated evidence correlating with landing
  at the cluster value), not fully explained by either factor alone.

### What V2 is intended to test

V2 tests whether a **structured probability-elicitation prompt** — requiring the model to
externalize its reasoning through explicit decomposition before naming a number — improves
cross-market probability discrimination and reduces this clustering, **without**:

- changing the underlying evidence collection, classification, or blindness pipeline,
- exposing Polymarket price data at any point,
- instructing the model to avoid `0.45` or round numbers specifically (which would itself be a
  new anchor, not a genuine fix),
- changing the model, provider, or generation (sampling) settings.

This is a **prompt-structure-only** intervention, isolated as a single variable, matching the
shadow experiment's Arm D design exactly.

---

## 2. Primary hypothesis

> **H1 (primary):** A structured decomposition prompt (V2) will reduce probability clustering
> relative to the existing single-shot prompt (V1), when both are run blind to market price
> against the same, matched evidence, without materially weakening reliability or calibration.

### Primary confirmatory criterion — directional, not magnitude-anchored

**The primary confirmatory criterion is directional, evaluated over the matched comparison
window (Section 6), and is deliberately not pinned to the shadow experiment's exact effect
sizes:**

> Across the 60 matched cycles, V2 shows **less probability clustering than V1** — operationalized
> as: V2's share of matched-cycle forecasts landing at exactly `0.45` is lower than V1's share over
> the same matched cycles.

This is the sole primary pass/fail signal for "clustering reduced." No specific magnitude (e.g.
"must drop by at least X percentage points") is preregistered as a threshold — a live effect of any
directionally-consistent size counts as a primary-criterion pass; the *size* of the effect is
then used as an input to distinguish Outcome A from Outcome B (Section 9), not to decide pass/fail
on its own.

**Secondary, corroborating, still-directional (not magnitude-anchored) signals**, tracked and
reported alongside the primary criterion but not independently required to pass it:

- V2's count of unique probability values across the matched-cycle set is higher than V1's.
- V2's between-market variance of mean forecast probability across the matched-cycle set is
  higher than V1's.

**Evidence-quality correlation (evidence count/overlap vs. forecast concentration) is explicitly
excluded from this criterion and from the Section 9 promotion gate entirely — see Section 8.** It
is tracked as exploratory analysis only.

### Observed shadow benchmarks (context only — never used as a pass/fail threshold)

For reference, the completed shadow experiment (Arm A vs. Arm D, identical model/settings/
evidence, prompt as the only variable) observed:

| Metric | Arm A (V1-equivalent) | Arm D (V2-equivalent) |
|---|---:|---:|
| Exact-`0.45` frequency | 75.0% (45/60) | 41.7% (25/60) |
| Unique probability values | 4 | 6 |
| Evidence-count correlation with distance-from-0.50 *(exploratory only, Section 8)* | 0.239 | 0.376 |

These numbers describe what was observed in one frozen, retrospective replay. They are reported
here for transparency and as later context for interpreting live effect size (Section 9) — **they
are not live-replication thresholds**, and a live V2 effect that is directionally consistent but
numerically smaller than this table does not fail the primary criterion.

### "Without materially weakening reliability or calibration"

Reliability is defined in Section 9's revised, observation-based reliability rule (no longer an
arbitrary percentage-point threshold). Calibration is defined in Section 7/9: no material
degradation in Brier score or log loss relative to V1 on markets that resolve during or after the
comparison window.

---

## 3. Secondary hypotheses

All secondary hypotheses below are directly supported by the completed shadow experiment
(`data/shadow_experiments/shadow_45pct_clustering_20260811_analysis.json`). No hypothesis is
included that the shadow experiment did not already provide directional evidence for.

**None of these secondary hypotheses independently gate V2 promotion.** Only Section 2's single
directional clustering criterion, Section 9 Step 1's reliability rule, and Section 9 Step 3's
calibration check determine Outcomes A–D. H2–H4 and H6 are **confirmatory-tracked**: reported
against live data as corroborating context for interpreting the primary result, using directional
(not magnitude-anchored) comparisons, consistent with Section 2. H5 and H7 are **exploratory
only**, per point 2 of this revision — evidence-quality correlation is never part of the
promotion gate, regardless of what it shows live.

- **H2 (confirmatory-tracked, directional only):** V2 will show more distinct probability values
  used on the 5-point grid (multiples of 0.05) than V1, even though both may remain ~100% on-grid
  at the aggregate level. *(Shadow evidence: Arm A 4 distinct grid points used vs. Arm D 6 — this
  hypothesis is scoped to unique-value count, not overall on-grid frequency, since the shadow
  experiment did not show a reduction in raw on-grid frequency — see Section 8's exploratory
  notes.)*
- **H3 (confirmatory-tracked, directional only):** V2 will show a lower exact-`0.45` frequency
  than V1 under matched evidence. This is the same relationship as the Section 2 primary
  criterion, restated here for the secondary-hypothesis list; no independent magnitude is
  required. *(Observed shadow benchmark, context only: 75.0% → 41.7% — see Section 2.)*
- **H4 (confirmatory-tracked, directional only):** V2 will show greater between-market variance in
  mean forecast probability than V1. *(Observed shadow benchmark, context only: `0.00353` →
  `0.00441`.)*
- **H5 (exploratory only — excluded from the promotion gate per point 2 of this revision):** V2
  will show a within-market forecast that is more responsive to market-specific evidence —
  operationalized as the evidence-count-vs-distance-from-0.50 correlation being higher under V2
  than V1. *(Observed shadow benchmark, context only: `r = 0.239` → `r = 0.376`.)* This is tracked
  and reported (Section 8) but never used to accept or reject H1, and never used in Section 9's
  decision rule.
- **H6 (confirmatory-tracked, feeds Section 9 Step 1's reliability rule):** V2 will not show a
  higher rate of degraded terminal outcomes (`ABSTAINED`, `ERROR`, `FAILED`) than V1 across matched
  cycles. *(Shadow evidence: Arm A had 0 `ABSTAINED`/0 `FAILED` out of 60; Arm D also had 0
  `ABSTAINED`/0 `FAILED` out of 60 — parity, not degradation. Note this is a **stronger**
  precedent than for thinking-mode arms: Arm B, not part of V2, had 2 `ABSTAINED` out of 60.)*
- **H7 (exploratory only — excluded from the promotion gate per point 2 of this revision):**
  Evidence quality (count, freshness, cross-market duplication) will remain associated with
  forecast discrimination under V2, not just V1 — i.e., the relationship is a property of the
  underlying evidence/model, not an artifact specific to the V1 prompt. *(Observed shadow
  benchmark, context only: the evidence-count correlation with distance-from-0.50 is positive in
  Arm A (`0.239`), Arm C (`0.274`), and Arm D (`0.376`) — present across every arm except
  thinking-mode Arm B, which V2 does not include.)* This is tracked and reported (Section 8) but
  never used to accept or reject H1, and never used in Section 9's decision rule.

No hypothesis regarding thinking mode, sampling-parameter changes, or absolute forecasting
accuracy improvement is included here — the shadow experiment either did not support such a claim
(Arm C: non-thinking sampling changes alone did not help) or cannot yet be evaluated (accuracy
requires resolved markets, addressed as a separate, longer-horizon evaluation in Section 7).

---

## 4. Exact V1 specification (frozen comparator)

Pulled directly from the repository at commit `a7eff636d7a089f1bd209abcfbbbc0918148ee97`.

| Component | Exact value | Source |
|---|---|---|
| Model | `qwen3:8b` | `app/config.py` (`llm_model`, `ollama_model`) |
| Provider | `ollama` (production; `AUTOMATED_PROVIDERS = {"ollama", "openai_compatible"}`) | `app/config.py` `llm_provider`; `app/ppi/blind_forecast.py` |
| Temperature | `0.15` (`GENERATION_TEMPERATURE`) | `app/ppi/blind_forecast.py:36` |
| Context length | `4096` (`GENERATION_NUM_CTX`) | `app/ppi/blind_forecast.py:37` |
| `top_p` / `top_k` / `seed` | Not set in the request; falls through to the Ollama/model's own defaults | `_call_ollama`'s `options` dict, `app/ppi/blind_forecast.py:285-299` |
| Thinking mode | Not set (`think` field omitted entirely from the API payload) — confirmed empirically this fully suppresses thinking on the current Ollama build (no separate `thinking` field returned, no `<think>` tags in `response`) | `_call_ollama`, `app/ppi/blind_forecast.py:285-299` |
| Prompt version | `fair_value_v0.1` (`PROMPT_VERSION`) | `app/ppi/blind_forecast.py:32`; `prompts/fair_value_prompt_v0_1.md` |
| Prompt hash | Computed per-forecast as `sha256(SYSTEM_INSTRUCTIONS + "\n\n" + build_prompt(packet))`, persisted to `LLMForecast.prompt_hash` — not a single fixed value, since the evidence-dependent prompt text varies per market/run | `_stable_hash`, `app/ppi/blind_forecast.py:138-139, 350-351` |
| Evidence collection | RSS/GDELT/JSON-API/manual source adapters (`app/ppi/evidence.py`), deduplicated per `(market_id, content_hash)` | `app/ppi/pipeline.py::_collect_market_evidence`, `app/ppi/evidence.py` |
| Evidence classification (strict mode) | `OllamaClassifier` only; a classification failure is recorded as an explicit `CLASSIFICATION_FAILED` item (`relevant=None`), never silently degraded to `DeterministicClassifier` | `app/ppi/evidence.py::insert_and_classify_candidate` |
| Evidence dedup | Per `(market_id, content_hash)`; the same underlying article can independently appear in multiple markets' evidence sets (confirmed responsible for cross-market overlap in `job_run_id=21`) | `app/ppi/evidence.py` |
| `MAX_EVIDENCE_ITEMS` | `8` | `app/ppi/blind_forecast.py:33` |
| `MAX_EVIDENCE_CHARS` | `6000` | `app/ppi/blind_forecast.py:34` |
| Evidence ordering | Most-recent-`published_at`-first, NULLs last | `build_blind_evidence_packet`, `app/ppi/blind_forecast.py:155-190` |
| Strict-mode evidence filter | `require_live_classifier=True` — excludes any evidence item not itself classified by `AUTOMATED_PROVIDERS`, even if `relevant=True` | `build_blind_evidence_packet`, `app/ppi/blind_forecast.py:155-166` |
| Blindness enforcement | `assert_blind_packet()` — recursively scans the packet for any of `FORBIDDEN_PACKET_KEYS` and raises if found | `app/ppi/blind_forecast.py:193-207` |
| Forbidden market-derived fields | `comparison_price, yes_price_displayed, no_price_displayed, yes_best_bid, yes_best_ask, no_best_bid, no_best_ask, yes_midpoint, no_midpoint, last_trade_price, spread, depth_1c, depth_3c, depth_5c, volume, liquidity, executable_buy_price, executable_sell_price, price_type, partisan_premium, fair_value, market_probability, polymarket_probability, raw_ppi, comparison_price_at_join, average_signed_premium, median_signed_premium, average_absolute_premium` | `FORBIDDEN_PACKET_KEYS`, `app/ppi/blind_forecast.py:42-71` |
| Retry policy | `MAX_RETRIES = 2` → up to 3 total attempts (1 initial + 2 retries) per forecast, each re-calling the model and re-attempting JSON extraction/schema validation on failure | `app/ppi/blind_forecast.py:35, 372-388` |
| Twice-daily cadence | 09:00 / 21:00 America/Toronto, native per-entry `timezone:` GitHub Actions schedule (`cron: "0 9 * * *"` / `"0 21 * * *"`, `timezone: "America/Toronto"`) | `.github/workflows/ppi-daily.yml` |
| PPI formula | `raw_ppi = polymarket_probability − llm_fair_value`, computed as `comparison_price − fair_value` only *after* the forecast row is already persisted | `join_forecast_with_price`, `app/ppi/blind_forecast.py:415-430` |
| Immutable forecast policy | A `LLMForecast` row already at `status == "OK"` is returned unchanged by any later call for the same `(market_id, run_slot)`; only a still-`FAILED`/`SKIPPED_PROVIDER` slot may be retried in place | `generate_blind_forecast`, `app/ppi/blind_forecast.py:302-330` |
| Canonical/noncanonical classification | `canonical` requires: `job.status != "FAILED"`, `pipeline_mode == "strict_llm_only"`, no forecast with `evidence_all_live_classified is False`, and `trigger_type in ("primary", "backup")` — computed once per run, never hand-edited | `compute_run_classification`, `app/ppi/run_classification.py` |
| Output schema | `BlindFairValueEstimate`: `fair_value` (required, `[0,1]`), `confidence` (required, `[0,1]`), `should_abstain` (default `False`), `rationale_short` (required, ≤700 chars), `key_uncertainties` (list, ≤5 items), `base_rate_notes` (default `""`, ≤500 chars) | `app/ppi/blind_forecast.py:125-131` |

### Exact V1 prompt text (verbatim, `app/ppi/blind_forecast.py`)

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

The full model prompt is `SYSTEM_INSTRUCTIONS + "\n\n" + USER_PROMPT_TEMPLATE.format(...)`.

---

## 5. Exact V2 specification

V2 changes **exactly one substantive forecasting component**: the probability-elicitation prompt
and its output schema, replacing `USER_PROMPT_TEMPLATE`/`BlindFairValueEstimate` with the
decomposition prompt and schema below. This is byte-for-byte the prompt already used as "Arm D" in
the completed shadow experiment (`scripts/run_shadow_experiment.py`, committed at
`a7eff636d7a089f1bd209abcfbbbc0918148ee97`), reproduced here verbatim as the frozen source of
record for this preregistration, independent of any future edits to that script.

### Exact V2 prompt text (verbatim, frozen)

The full model prompt is identical in structure to V1: `SYSTEM_INSTRUCTIONS + "\n\n" +
DECOMPOSED_PROMPT_TEMPLATE.format(...)` — **`SYSTEM_INSTRUCTIONS` is unchanged from V1**, quoted
in full in Section 4 above. Only the user-turn template and output schema differ:

```
DECOMPOSED_PROMPT_TEMPLATE = """Estimate the blind fair value for this option-level event contract.

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

Work through this explicitly, in order, before giving your final number:
1. base_rate: What is the historical/structural base rate for an outcome like this, before looking at specific evidence?
2. market_specific_evidence: What evidence specifically supports a higher probability?
3. evidence_against: What evidence specifically supports a lower probability, or contradicts the case above?
4. uncertainty: What remains genuinely uncertain or unresolved?
5. fair_value: Your final probability estimate, informed by 1-4.

OUTPUT JSON SCHEMA:
{{
  "base_rate": string, max 500 characters,
  "market_specific_evidence": string, max 800 characters,
  "evidence_against": string, max 800 characters,
  "uncertainty": string, max 500 characters,
  "fair_value": number between 0 and 1,
  "confidence": number between 0 and 1
}}

Return ONLY valid JSON matching this schema. No markdown. No commentary outside JSON.
"""
```

### Exact V2 output schema (verbatim, frozen)

```python
class DecomposedProbabilityEstimate(BaseModel):
    base_rate: str = Field(min_length=1, max_length=500)
    market_specific_evidence: str = Field(min_length=1, max_length=800)
    evidence_against: str = Field(min_length=1, max_length=800)
    uncertainty: str = Field(min_length=1, max_length=500)
    fair_value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
```

This decomposition satisfies the required five-step structure exactly:
**(1) base rate/prior → (2) evidence for → (3) evidence against → (4) uncertainty → (5) final
probability.**

### Explicit invariants — what V2 does NOT change

The following are frozen exactly as specified in Section 4, and any deviation from them is not a
valid V2 implementation under this preregistration (see Section 11):

- Does **not** instruct the model to avoid `0.45`.
- Does **not** instruct the model to avoid round numbers generally.
- Does **not** expose Polymarket price, bid, ask, spread, volume, liquidity, or any derived
  market signal — `assert_blind_packet`/`FORBIDDEN_PACKET_KEYS` apply identically to V2.
- Does **not** change the model (`qwen3:8b` via `ollama`, unchanged).
- Does **not** change temperature (`0.15`, unchanged).
- Does **not** change context size (`4096`, unchanged).
- Does **not** enable thinking mode (`think` remains omitted from the request, unchanged).
- Does **not** change evidence collection, source adapters, or discovery logic.
- Does **not** change `MAX_EVIDENCE_ITEMS` (`8`, unchanged), `MAX_EVIDENCE_CHARS` (`6000`,
  unchanged), evidence ordering, or the strict-mode live-classification filter.
- Does **not** change the retry policy (`MAX_RETRIES = 2`, unchanged).
- Does **not** change JSON-extraction parsing behavior (`_extract_json_object`'s `<think>`-tag
  stripping and brace-matching fallback apply identically; V2 simply validates the extracted
  object against `DecomposedProbabilityEstimate` instead of `BlindFairValueEstimate`).

---

## 6. Parallel-run design

### Comparison window: 60 canonical twice-daily cycles (not a fixed 30-calendar-day window)

**Recommendation: 60 canonical cycles**, not 30 calendar days, chosen for the following reason,
specific to this repository's own architecture and recent operational history: the twice-daily
cadence is already keyed on discrete `run_slot`/`trigger_type` cycles (`primary`/`backup`), not
raw calendar time — `determine_run_slot`, `run_key = ppi-daily:<date>:<slot>`, and
`compute_run_classification` all reason in terms of *cycles*, not *days*. This project has also
had a directly observed history of infra-caused missed cycles (self-hosted-runner failures across
multiple consecutive scheduled runs prior to the fixes merged in PRs #3–#7 this cycle of work) — a
fixed 30-calendar-day window would silently shrink the *actual number of comparable observations*
if any future cycle fails for infrastructure reasons, without that being visible in the window
definition itself. A 60-canonical-cycle window guarantees exactly 60 real, comparable
V1-and-V2-paired observations regardless of calendar gaps, and only extends in wall-clock time
(never in comparison power) if cycles are missed — which is the more conservative, more
statistically meaningful choice for a comparison whose entire point is sample size.

60 cycles at 2/day, assuming no missed cycles, corresponds to approximately 30 calendar days — the
two options are not in tension in the common case; the cycle-based framing is simply more robust
to the specific operational risk this project has already experienced.

### Matched cycle definition (precise, exhaustive — all five must hold)

A canonical cycle counts toward the 60-cycle comparison window **only if all five of the
following hold** for that cycle:

1. **Same market universe.** V1 and V2 forecasts within the cycle's `JobRun` cover the same set of
   enabled, tracked markets (currently 12).
2. **Same frozen evidence packet.** The V1 generation call and the V2 generation call for a given
   market receive byte-identical evidence input.
3. **Generated back-to-back in the same `JobRun`.** V1 and V2 generation for a given market are
   sequenced consecutively within one canonical run, not split across separate runs or separate
   scheduled slots.
4. **Independently persisted.** V1 and V2 rows are distinguishable by a `methodology_version`
   field (Section 13) at the schema level, not merely by prompt version alone, so a query can
   never accidentally mix them; persisting a V2 forecast must never touch, retry, or supersede a
   V1 row for the same market/slot, and vice versa.
5. **No evidence refresh between V1 and V2.** No evidence-discovery/classification step for that
   market executes between the V1 call and the V2 call.

Blindness to Polymarket price applies identically to both, unchanged from V1 today —
`assert_blind_packet` applies to both V1's and V2's packet before every call — but is not itself
one of the five matched-cycle conditions above, since it is a structural invariant of every
canonical forecast (Section 4/5/12), not a property specific to *pairing* V1 with V2.

**Mechanically**, condition 2 and condition 5 are the same underlying guarantee, stated at
different levels: `build_blind_evidence_packet` re-queries `EvidenceItem` fresh each time it is
called (no evidence-packet object is passed between V1 and V2 — each independently queries the
same underlying rows), so as long as no new evidence is inserted for that market between the two
calls, both receive a byte-identical evidence set. This makes evidence-packet parity a sequencing
guarantee (condition 3 enforces the ordering that makes conditions 2 and 5 hold), not a caching
mechanism.

### Never backfill a missed pair

If a scheduled cycle fails to produce a matched V1/V2 pair for any reason (infra failure, a
market temporarily disabled, an evidence-pipeline error affecting one side but not the other,
partial `JobRun` failure) — that cycle is **skipped entirely for comparison purposes, permanently**.
It is never reconstructed after the fact, never regenerated against evidence as it existed on the
missed date, and never substituted with a later cycle relabeled to fill the gap. The window simply
requires 60 *actually matched* cycles, arriving whenever they arrive; a missed cycle only extends
wall-clock time to reach 60, exactly as already stated above. This is a direct application of
Section 10's "V2 never backfills historical timestamps" rule to the comparison-window mechanism
specifically.

### Public visibility during the comparison window

Resolved explicitly, extending this project's existing auto-publish-on-canonical rule
(`app/ppi/public_forecast.py`) rather than inventing a new approval path:

- **V1 remains the sole headline canonical PPI series** throughout the entire comparison window.
  V1's existing auto-publish-once-persisted behavior is unchanged by this preregistration.
- **V2 forecasts may be automatically exposed publicly, but only as a clearly, distinctly labeled
  experimental comparison series** — never merged into, displayed as, or capable of being confused
  with the headline `PPI`/`raw_ppi` series. The same automatic, no-selective-approval publication
  model that already governs V1 applies to V2's experimental surface: a canonical V2 forecast
  publishes to the experimental surface once persisted, and a human may only `FLAG` it for a
  genuine data-integrity concern (suppressing its display), never selectively approve or edit it.
  This is a direct extension of the existing rule in
  `.claude/rules/research-integrity.md` ("a canonical forecast publishes automatically once
  persisted; data-integrity review may only flag it") to V2's separate series, not a new or looser
  rule.
- **V2 must not enter the headline PPI index, the `raw_ppi`/standardized aggregate views, or
  replace V1 in any public surface**, under any circumstance, until Outcome A (Section 9) is
  formally selected, dated, and committed as a decision record.
- **No selective human publication approval exists for either series.** A human reviewer's role is
  limited to flagging suspected data-integrity problems on either series; publication itself
  remains fully automatic for both, exactly as today for V1.

The exact endpoint/field naming for the experimental comparison surface (e.g. a distinct API
route or a distinctly named export field, separate from the existing public export schema) is not
fixed by this preregistration and is listed as an open implementation decision in Section 15.

---

## 7. Primary evaluation metrics

Preregistered now, before any live V2 observation exists.

### Discrimination / distributional metrics (available immediately, do not require resolution)

- Frequency of forecasts exactly equal to `0.45`.
- Number of unique forecast values (per run, and cumulative over the comparison window).
- Share of forecasts on the 5-point grid (`fair_value` is a multiple of `0.05`, within floating
  point tolerance).
- Between-market variance of mean forecast probability.
- Within-market temporal variance — the variance of a single market's `fair_value` across
  successive primary/backup cycles within the comparison window (distinct from the shadow
  experiment's "within-market variance," which was across repeated generations against a *frozen*
  evidence snapshot; this live metric is across *time*, as evidence genuinely evolves).
- Mean absolute distance from `0.50` (`mean(|fair_value − 0.50|)`), as a coarse discrimination
  proxy independent of direction.
- `ABSTAINED` rate.
- `ERROR`/`FAILED` rate (forecast generation failing to produce a valid parse after retries, or
  the underlying call erroring).

### Accuracy / calibration metrics (require resolved markets — evaluated on whatever subset
resolves during or shortly after the comparison window; see Section 9 for how an insufficient
resolved sample is handled)

- Brier score (V1 vs. V2, per resolved market and pooled).
- Log loss (V1 vs. V2, per resolved market and pooled).
- Calibration (predicted-probability vs. observed-frequency binning, to the extent sample size
  allows).
- V1-vs-V2 score differential (paired, same market/resolution).
- Polymarket-vs-V1 score differential (existing comparison, unchanged).
- Polymarket-vs-V2 score differential (new).

**Explicit statement, preregistered:** an improvement in discrimination metrics alone (fewer
`0.45`s, more unique values, higher between-market variance) **does not by itself establish that
V2 is a better forecaster than V1.** A model that discriminates more between markets but is
worse-calibrated is not an improvement. Discrimination metrics answer "did V2 break the clustering
habit"; calibration/accuracy metrics answer "is V2 actually a better forecaster" — both must be
considered, and neither substitutes for the other, per Section 9's decision rule.

---

## 8. Evidence-quality analysis

**Entirely exploratory, per point 2 of this revision.** Every analysis in this section is
directional, hypothesis-generating only, and is **excluded from the Section 2 primary criterion
and the Section 9 promotion gate in full** — regardless of what it shows in live data, it never by
itself accepts, rejects, or gates H1, H5, or H7, and is never a condition in Section 9's decision
steps. It exists to help interpret *why* a primary-criterion result occurred, not to help decide
*whether* V2 is promoted.

- Relationship between per-market evidence count and forecast distance-from-`0.50`, under V2 live
  data, reported alongside the shadow-experiment observed benchmarks (`r ≈ 0.24` for the
  V1-equivalent Arm A, `r ≈ 0.38` for the V2-equivalent Arm D — see Section 2) purely as
  descriptive context, not as a replication target.
- Evidence freshness (recency of `published_at` relative to generation time) and its relationship
  to forecast concentration, live.
- Duplicated/shared evidence (by `content_hash`) across markets, live, and whether the same
  cross-market-overlap pattern observed in `job_run_id=21` persists, changes, or is diluted as the
  evidence corpus grows over the comparison window.
- Market-specific vs. generic evidence (qualitative categorization of evidence content) and its
  relationship to forecast concentration — this was not quantitatively operationalized in the
  shadow experiment (which only measured evidence *count* and *overlap*, not a generic/specific
  classification) and is therefore exploratory only under this preregistration.

---

## 9. Cutover decision rule

No informal "V2 looks better" judgment is sufficient. At the end of the 60-cycle comparison
window, the following framework applies, in order:

### Step 1 — Operational reliability gate (must pass to proceed to Step 2)

**Observation-based rule, replacing the earlier arbitrary ≤2pp `ERROR`/≤5pp `ABSTAIN` percentage
thresholds**, per point 3 of this revision:

1. For every matched cycle × market observation in the 60-cycle window (Section 6), classify each
   side's terminal outcome using the ordinal ranking **`OK` > `ABSTAINED` > `FAILED`/`ERROR`**
   (best to worst). *(Judgment call #1, flagged explicitly: this ordering treats an abstention as
   strictly better than a failure and strictly worse than a completed forecast — a reasonable but
   not the only defensible ranking; e.g. one could argue a confidently wrong `OK` is worse than an
   honest `ABSTAINED`. This preregistration fixes the simpler ordinal ranking above before any
   live V2 data exists.)*
2. For each matched observation, compare V1's and V2's ranked outcome: **V2-better** (V2 ranks
   strictly above V1), **V2-worse** (V2 ranks strictly below V1), or **tie** (equal rank).
3. **Reliability rule:** V2 passes Step 1 if, summed across all matched observations in the
   window, `count(V2-worse) ≤ count(V2-better)`. *(Judgment call #2, flagged explicitly: this
   requires V2-worse to not exceed V2-better — a tie at the margin (equal counts) is treated as a
   pass, not a fail. A stricter rule (`V2-worse < V2-better`, i.e. V2 must be strictly more
   reliable, not merely no-worse-on-net) would also have been defensible; the more permissive
   `≤` form is preregistered here because Step 1 is a gate against *material* reliability
   regression, not a requirement that V2 be strictly more reliable than V1 — reliability
   superiority is not itself a promotion goal, avoiding regression is.)*

Failing this gate alone is sufficient to select **Outcome C** (V1 remains canonical) regardless of
discrimination results — a more expressive prompt that also breaks more often on net is not an
improvement.

### Step 2 — Discrimination replication check (directional only, per points 1 and 2 of this revision)

**This check uses only directional (not magnitude-anchored) signals, and excludes evidence-count
correlation entirely** — consistent with Section 2's primary criterion and Section 3's
confirmatory/exploratory labeling:

- **Primary signal (required):** does live matched-cycle data show V2's exact-`0.45` frequency
  lower than V1's, per Section 2's primary criterion?
- **Corroborating signals (not individually required):** does live data show V2's unique-value
  count higher than V1's (H2), and/or V2's between-market variance higher than V1's (H4)?

Evaluated as:

- If the **primary signal fails** (V2's clustering is not lower than V1's, or is higher) →
  **Outcome D** (neither promoted; the shadow effect did not generalize beyond the frozen replay;
  preregister a V3 study investigating why, e.g. evidence evolving over time, live-vs-frozen
  prompt-context interactions, or a genuinely smaller/absent live effect).
- If the **primary signal holds** and **at least one corroborating signal also holds** → proceed
  to Step 3.
- If the **primary signal holds but neither corroborating signal holds** → treated as a partial,
  ambiguous replication → **Outcome B** (extend the comparison window; the core directional effect
  is present but not yet well-corroborated).

### Step 3 — Calibration/accuracy check, resolution-sample-aware

- If enough markets have resolved during the window for a meaningfully powered Brier/log-loss
  comparison (this preregistration does not fix a specific minimum count in advance, since the 12
  tracked markets' resolution dates are calendar/election-driven and not fully known at
  preregistration time — but the comparison must not be treated as conclusive on fewer than
  several resolved markets): use it as the primary accuracy signal.
- If the window ends with too few or zero resolutions (plausible, since several tracked contracts
  resolve around the November 2026 midterms/other election dates, which may fall after a 60-cycle
  window beginning in mid-August 2026): **do not require resolved outcomes to make an interim
  decision.** Distinguish explicitly:
  - **Interim adoption criteria** (usable without resolutions): Steps 1 and 2 above, i.e.
    operational reliability plus discrimination replication.
  - **Final accuracy evaluation**: deferred until enough resolutions exist, tracked as a follow-on
    obligation regardless of which interim outcome is chosen.

### Possible outcomes

- **A — V2 replaces V1 for future canonical forecasting.** Requires passing Steps 1 and 2, and
  either passing Step 3 or explicitly deferring Step 3 with a documented follow-on evaluation
  commitment (interim promotion pending final accuracy confirmation).
- **B — Parallel period is extended.** Chosen if Step 2's primary signal holds but neither
  corroborating signal does (a partial, ambiguous replication) — more cycles are needed to
  distinguish a real but weak effect from noise.
- **C — V1 remains canonical.** Chosen if Step 1 fails (net reliability regression).
- **D — Neither is promoted; a V3 study is preregistered.** Chosen if Step 2's primary signal
  fails (V2's clustering is not directionally lower than V1's) — the negative result is itself a
  research finding and must be documented, not discarded.

Any of these four outcomes must be recorded, dated, and committed as a documented decision record
(a follow-on document, not a silent operational change) before it takes effect.

---

## 10. Historical integrity

Stated explicitly, restating and applying this project's existing research-integrity rules to V2
specifically:

- **V1 forecasts remain immutable forever.** No V2-related work edits, deletes, or reinterprets
  any existing `LLMForecast` row's `fair_value`, `confidence`, `raw_response`, or any other
  model-output field.
- **V2 never backfills historical V1 timestamps.** V2 observations begin at the comparison
  window's recorded start timestamp (Section 15) and are never retroactively generated for past
  dates using current evidence.
- **`job_run_id=21` remains canonical historical V1 data.** It is not reclassified, superseded, or
  excluded as a result of this preregistration or any future V2 result — it is the observation
  that motivated this study, not a data point to be revised.
- **Shadow experiments remain diagnostic/noncanonical.** The Arm A–D data under
  `data/shadow_experiments/` is not, and never becomes, part of the canonical PPI series, is never
  read by `scripts/export_public_bundle.py` or the Streamlit review UI's canonical views, and is
  not used as a substitute for live V2 production data in the Section 9 decision.
- **Methodology-version changes are timestamped and append-only.** This document, once committed,
  is not edited to retroactively change hypotheses or thresholds after live V2 data exists;
  amendments happen via the deviation process (Section 11) or a superseding, separately dated
  document.

---

## 11. Deviations policy

Any change to the implementation, environment, or process after this document is committed but
before or during the comparison period must be logged as a **deviation**, in a dated addendum to
this document (or a linked deviation log), classified as one of:

- **Infrastructure-only fix** (e.g. a self-hosted-runner, scheduling, or CI fix that does not
  touch prompt text, model, settings, or evidence logic) — does not invalidate the comparison
  window; log and continue.
- **Data-source outage** (e.g. a source adapter temporarily failing, reducing evidence discovery
  for some markets) — does not invalidate the window by itself, but must be logged since it can
  affect the evidence-quality analysis (Section 8); large or sustained outages should be flagged
  for Step 2/3 interpretation caveats.
- **Methodology change** (any change to V1's or V2's prompt text, schema, model, temperature,
  context size, thinking-mode state, evidence pipeline, `MAX_EVIDENCE_ITEMS`, retry policy, or
  parsing logic during the window) — **must** either (a) invalidate and restart the comparison
  window from that point, or (b) be recorded as the start of a new, separately versioned
  methodology (V2.1, V3, etc.) with its own preregistration, per this project's existing rule that
  "any model change starts a clearly versioned series and requires a documented comparison." A
  methodology change is never silently absorbed into the existing V1-vs-V2 comparison.
- **Emergency integrity fix** (e.g. a discovered blindness leak, a discovered parser bug producing
  invalid data) — treated as a methodology change for versioning purposes (Section 11's
  methodology-change branch applies), but logged with explicit urgency/severity context, since the
  underlying defect being fixed may itself invalidate some already-collected window data
  retroactively (to be assessed case-by-case, documented, never silently discarded).

---

## 12. Stop conditions

An observation (V1 or V2) must **not** be generated, or must be generated and then explicitly
marked noncanonical (never silently treated as a normal successful cycle), under any of:

- Ollama is unavailable (`ops/self-hosted-runner/preflight.sh`'s existing reachability check
  fails).
- The resolved model is not exactly the pinned model (`qwen3:8b`) — no silent fallback to a
  different tag/quantization.
- A deterministic fallback would otherwise be required — under `strict_llm_only`/canonical mode
  this already cannot happen without the run failing loudly (`generate_blind_forecast`'s
  `AUTOMATED_PROVIDERS` guard), and V2 inherits this guarantee unchanged.
- The evidence pipeline fails for a market (a source adapter error propagates rather than being
  silently swallowed as zero evidence when it should be an error).
- A blindness violation is detected (`assert_blind_packet` raises) — this must hard-fail the
  observation for that market, for both V1 and V2 identically, never silently strip the offending
  field and continue.
- The model output is parser-invalid after exhausting `MAX_RETRIES` — recorded as `FAILED`,
  exactly as V1 already does; V2 must record `FAILED` under the same terms, never substituting a
  default or partial-decomposition value.
- The V1 and V2 evidence packets for the same nominal observation are found to be
  mismatched/non-identical (violating Section 6's parity requirement) — that paired observation
  must be excluded from the Section 7/9 comparison (though each side's own forecast may still be
  independently valid and persisted), and logged as a deviation per Section 11.

---

## 13. Version identifiers

**Methodology and prompt identifiers are kept strictly separate**, per point 5 of this revision —
`methodology_version` names the overall forecasting design (prompt + schema + decision framework
as a unit), while `prompt_version` names only the specific prompt/schema pairing. This separation
matters because a future methodology could, in principle, reuse a prompt version across a minor
methodology revision, or a prompt could be patched (typo fix, formatting) without constituting a
new methodology — collapsing the two into one identifier would make that distinction
unrepresentable.

| Identifier | V1 value | V2 value |
|---|---|---|
| `methodology_version` | `ppi_v1` | `ppi_v2` |
| `prompt_version` | `fair_value_v0.1` | `fair_value_decomposition_v1` *(proposed; to be assigned exactly this value if/when V2 is implemented — not yet created in `app/ppi/blind_forecast.py` or `prompts/`)* |
| Model identifier (separate field, unchanged by V2) | `qwen3:8b` (`model_provider="ollama"`, `model_name="qwen3:8b"`) | `qwen3:8b` (`model_provider="ollama"`, `model_name="qwen3:8b"`) — unchanged |
| Generation-settings version (separate field, unchanged by V2) | `{"temperature": 0.15, "num_ctx": 4096, "max_retries": 2}` (persisted per-forecast in `generation_params_json`, unversioned as a label but byte-identical in content) | Identical dict, byte-for-byte — unchanged |
| Evidence-pipeline version (separate field, formalized by this revision) | `evidence_pipeline_v1` *(proposed; no `evidence_pipeline_version` field currently exists in the schema — this preregistration formalizes it as an explicit, separate field per point 5, since V2 must be provably running against the *same* evidence-pipeline version as V1, not merely an unversioned assumption of sameness)* | `evidence_pipeline_v1` — identical value, since V2 introduces no change to evidence-pipeline behavior (Section 5's invariants) |

`methodology_version` and `evidence_pipeline_version` do not currently exist as columns on
`LLMForecast`; introducing both is listed as required schema work in Section 15, not performed by
this document.

---

## 15. Implementation checklist

**None of the following is performed by this document.** Listed here as the concrete, reviewable
scope of work that becomes *allowed* only after this preregistration is reviewed and committed —
not authorization to begin it now.

- [ ] Freeze the V2 prompt as a committed file (`prompts/fair_value_decomposition_v1.md`, matching
      the `prompt_version = "fair_value_decomposition_v1"` identifier from Section 13), following
      the existing `prompts/fair_value_prompt_v0_1.md` documentation convention.
- [ ] Add `methodology_version` (`ppi_v1`/`ppi_v2`) and `evidence_pipeline_version`
      (`evidence_pipeline_v1`) fields/support to `LLMForecast` (and any other tables that need to
      distinguish V1/V2), via an additive migration, following the existing pattern in
      `scripts/migrate_db.py`'s `ADDITIVE_COLUMNS`.
- [ ] Implement dual-generation: both V1 and V2 forecasts generated from the same evidence packet
      per Section 6's matched-cycle design, for every canonical run.
- [ ] Persist V1 and V2 forecasts as fully independent, separately identified rows — never
      overwriting, never sharing a row.
- [ ] Implement V2's experimental comparison surface per Section 6's public-visibility resolution:
      automatic publication (no selective human approval, `FLAG`-only review, mirroring V1's
      existing rule) to a distinctly labeled experimental series, structurally separated from the
      headline PPI export/index so V2 can never be confused with or substitute for V1. **Exact
      endpoint/field naming is an open decision, not fixed by this preregistration** — flagged
      again explicitly in the judgment-call summary.
- [ ] Add regression tests: dual-generation produces independently correct V1 and V2 rows from
      identical evidence input; `methodology_version` and `evidence_pipeline_version` are never
      ambiguous; V2's schema validation rejects a response missing any of the five required
      decomposition fields; blindness checks apply identically to both; the experimental surface
      never leaks into the headline public export.
- [ ] Re-verify blindness for the V2 path specifically (`assert_blind_packet` against V2's packet
      construction, even though packet construction itself is unchanged from V1).
- [ ] Implement Section 9 Step 1's reliability rule (ordinal terminal-outcome ranking, paired
      matched-observation comparison, `count(V2-worse) ≤ count(V2-better)`) as a computable,
      testable function, not an ad hoc end-of-window manual tally.
- [ ] Record the comparison window's start timestamp explicitly and durably (e.g. in a committed
      decision-log document, not only in commit metadata) at the moment dual-generation actually
      begins in production — this is the timestamp Section 6's 60-cycle count runs from, and the
      point after which a missed cycle is permanently skipped, never backfilled.

---

*End of preregistration. No production code was modified, no forecast was generated, and no V1
historical data was altered in the creation of this document.*
