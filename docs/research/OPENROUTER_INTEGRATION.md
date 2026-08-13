# OpenRouter / DeepSeek integration

A second, separately-labelled blind forecast model series, alongside the primary Qwen3:8b/Ollama
series. This document covers setup, provenance, cost logging, and failure behavior. It does not
describe a live, preregistered comparison series -- see "Methodology status" below.

## Exact model

| Field | Value |
|---|---|
| Provider | `openrouter` |
| API base URL | `https://openrouter.ai/api/v1` |
| Pinned model | `deepseek/deepseek-v4-flash-0731` |
| Canonical OpenRouter slug | `deepseek/deepseek-v4-flash-20260731` (`deepseek-v4-flash-0731` is the alias actually requested and persisted; never substitute the canonical form) |
| Context length | 1,048,576 tokens |
| Max completion tokens (provider ceiling) | 384,000 |
| Listed pricing | $0.08 / 1M input tokens, $0.18 / 1M output tokens (`GET /api/v1/models`, checked live -- not guessed) |
| Reasoning support | `mandatory: false`, `default_enabled: true`, `supported_efforts: ["max", "high", "low"]`, `default_effort: "high"` |

The model is never requested by alias, and OpenRouter's auto-routing/`:free`/`:latest` suffixes are
never used -- `openrouter_model` in `app/config.py` is the literal pinned string above.

## How this differs from the local Qwen series

| | Qwen3:8b (primary) | DeepSeek V4 Flash 0731 (comparison) |
|---|---|---|
| `model_provider` | `ollama` | `openrouter` |
| Where it runs | Self-hosted Mac (`ppi-daily.yml`, `[self-hosted, macOS, ppi]`) | Any GitHub-hosted runner (`ppi-openrouter-diagnostic.yml`, `ubuntu-latest`) -- pure HTTPS API call, no local model to reach |
| Canonical / headline series | Yes | No -- diagnostic/experimental only until a separate methodology decision |
| Auto-published | Yes, automatically, once persisted | Not applicable yet -- current usage never writes to the database at all (see below) |
| Cost | Free (local compute) | Metered (usage logged per call, see below) |

## Provider architecture

`app/ppi/blind_forecast.py` gained a `ProviderConfig` dataclass and a `_call_openrouter` function,
reusing the exact same evidence packet, blindness enforcement (`assert_blind_packet`), prompt
construction, JSON extraction, and retry-loop code the Qwen path already uses --
`generate_blind_forecast(..., provider_config=...)` is the single shared entry point for both
providers. `_call_openrouter` mirrors `app.ppi.classifier.OpenAICompatibleClassifier`'s wire shape
(bearer auth, `/chat/completions`, `response_format: json_object`), extended with OpenRouter's
unified `reasoning` object and token-usage/served-model accounting.

`LLMForecast`'s uniqueness was widened from `(market_id, run_slot)` to `(market_id, run_slot,
model_provider)` (`scripts/migrate_db.py`), so a second provider's forecast for the same
market/slot is always an independent row -- it can never silently overwrite or be skipped in favor
of the primary series' row, and vice versa. `compute_run_classification`
(`app/ppi/run_classification.py`) and the `BlindIndexRun` aggregate rollup are both scoped to
`PRIMARY_SERIES_PROVIDERS = {"ollama"}`, so a DeepSeek forecast sharing a `run_key` with a
canonical Qwen run can never affect Qwen's own classification or aggregate premium.

**This capability is prepared but not activated**: no scheduled workflow currently calls
`generate_blind_forecast` with an OpenRouter `provider_config`. `ppi-daily.yml` is unmodified.

## Setup

### GitHub Actions secret

```bash
gh secret set OPENROUTER_API_KEY --repo RajJGandhi/partisan-premium-index
```

Paste the key interactively when prompted. Never commit a real key; `.env.example` only documents
an empty placeholder.

### Local environment

```bash
# .env
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731
OPENROUTER_TIMEOUT_SECONDS=90
OPENROUTER_MAX_OUTPUT_TOKENS=2048
```

### Local testing

```bash
PYTHONPATH=. python -m pytest tests/test_openrouter_provider.py -q
```

All HTTP calls are mocked in the normal test suite -- running the tests never spends OpenRouter
credits, and CI (`ci.yml`) never sets `OPENROUTER_API_KEY`, so it cannot spend credits either.

## GitHub Actions

`.github/workflows/ppi-openrouter-diagnostic.yml` is `workflow_dispatch`-only, `runs-on:
ubuntu-latest`. It reads a frozen, already-committed evidence-packet JSON (default:
`data/shadow_experiments/shadow_45pct_clustering_20260811_frozen_inputs.json`, the same 12-market
packet used to diagnose the Qwen clustering anomaly) and runs
`scripts/run_shadow_experiment.py`'s Arm F (or any other arm) against it. It never sets
`DATABASE_URL` and never touches `LLMForecast`/`JobRun` -- results are uploaded only as a workflow
artifact, exactly like the existing Qwen shadow experiments this project already uses.

## Model provenance

Every forecast row (when generated via the DB-backed path) and every diagnostic result (via the
shadow-experiment script) records: `model_provider`, `model_name` (the pinned request slug),
`prompt_version`, exact generation settings (temperature, `reasoning`), raw response, parsed
probability, status, and -- once available -- the served-model identifier OpenRouter's own
response echoes back and token usage. Token/cost metadata lives inside `generation_params_json`
(or the shadow-experiment result's `usage` field) as descriptive JSON; it never influences
`fair_value`/`confidence`/`status`.

## Cost logging

Every OpenRouter call records, when available: `prompt_tokens`, `completion_tokens`,
`total_tokens`, `served_model`, and the exact `reasoning` configuration used. Estimated cost is
computed post hoc from OpenRouter's listed per-token pricing (see the table above); it is never
computed by the model itself and never fed back into the forecast.

Safeguards: `openrouter_max_output_tokens` (default 2048) caps response length;
`openrouter_timeout_seconds` (default 90) bounds each request; the shared `MAX_RETRIES = 2` policy
(3 total attempts) applies identically to both providers. There is no automatic retry loop beyond
that, and no scheduled/recurring OpenRouter job exists yet -- every OpenRouter call to date has
been a manually-dispatched, bounded diagnostic run.

## Failure behavior

A failed DeepSeek request (timeout, rate limit, malformed response, missing API key, HTTP error)
is always recorded as an explicit `FAILED` status. It never falls back to Qwen, to a deterministic
value, or to any other model -- see `tests/test_openrouter_provider.py` for the regression tests
covering each failure mode, and
`tests/test_run_shadow_experiment.py::test_arm_f_failed_request_records_failed_status_no_qwen_fallback`
for the same guarantee in the diagnostic script.

## Methodology status

DeepSeek forecasts are a diagnostic/experimental comparison series, not a preregistered live
comparison. The first integration test uses the exact V1 prompt (`fair_value_v0.1`), with
reasoning explicitly disabled (`reasoning: {"enabled": false}`) and `temperature=0.15` -- chosen to
match Qwen Arm A as closely as this model allows, so model identity is the only deliberately-varied
comparison in that first pass, not reasoning mode or temperature. A live, preregistered
DeepSeek-vs-Qwen comparison series (matched cycles, a decision rule, public-visibility rules) does
not exist and is out of scope for this integration -- it would require its own preregistration
document, analogous to `docs/research/PPI_V2_PREREGISTRATION.md`, before any live comparison
begins.
