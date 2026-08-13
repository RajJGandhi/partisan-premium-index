"""Primary blind-LLM (Qwen) fair-value forecast generation.

This is the canonical, database-backed implementation of the blind forecast contract
previously prototyped in ``scripts/run_llm_fair_values.py`` against flat CSV files. It reuses
the same prompt contract (``fair_value_v0.1``, see ``prompts/fair_value_prompt_v0_1.md``) so
historical CSV runs and database runs remain comparable, but persists immutable, append-only
rows keyed by ``(market_id, run_slot)`` instead of timestamped files.

Blindness contract: the evidence packet built here is sourced only from ``Market`` identity
fields and DB-stored ``EvidenceItem`` rows. It must never include Polymarket price, bid, ask,
spread, volume, liquidity, or any other market-derived field. ``assert_blind_packet`` enforces
this at runtime so a future field added to the packet cannot silently leak market consensus.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import median
from typing import Any

import requests
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import BlindIndexRun, EvidenceItem, JobRun, LLMForecast, Market, MarketSnapshot

PROMPT_VERSION = "fair_value_v0.1"
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 6000
MAX_RETRIES = 2
GENERATION_TEMPERATURE = 0.15
GENERATION_NUM_CTX = 4096

AUTOMATED_PROVIDERS = {"ollama", "openai_compatible", "openrouter"}

# The headline/canonical PPI series' provider(s). Any other provider (e.g. openrouter) is a
# separately-labelled comparison series that must never be pulled into the primary aggregate or
# run-classification logic -- see PRIMARY_SERIES_PROVIDERS' two call sites below and in
# app.ppi.run_classification.compute_run_classification.
PRIMARY_SERIES_PROVIDERS = {"ollama"}

# Attribution headers OpenRouter uses for its public leaderboards -- not security-sensitive, safe
# to hardcode (see https://openrouter.ai/docs/quickstart).
OPENROUTER_REFERER = "https://partisan-premium-index.pages.dev"
OPENROUTER_APP_TITLE = "Partisan Premium Index"


@dataclass(frozen=True)
class ProviderConfig:
    """Explicit model-provider selection for one forecast call.

    ``generate_blind_forecast`` defaults to building one of these from global ``settings`` when
    none is passed, preserving exact current (Qwen/Ollama) behavior for every existing caller.
    Passing one explicitly is how a second, separately-versioned model series (e.g. OpenRouter)
    is generated without touching the primary series' config path at all.
    """

    provider: str  # matches LLMForecast.model_provider, e.g. "ollama" or "openrouter"
    base_url: str
    model: str
    api_key: str = ""
    timeout: int = 90
    temperature: float = GENERATION_TEMPERATURE
    extra_headers: dict[str, str] = field(default_factory=dict)
    reasoning: dict[str, Any] | None = None  # OpenRouter's unified reasoning object, or None
    max_output_tokens: int | None = None


def default_provider_config(settings: Any) -> ProviderConfig:
    """The exact provider selection generate_blind_forecast has always used, unchanged."""
    return ProviderConfig(
        provider=settings.llm_provider,
        base_url=settings.llm_base_url or settings.ollama_base_url,
        model=settings.llm_model or settings.ollama_model,
        timeout=settings.llm_timeout_seconds,
    )


def openrouter_provider_config(
    settings: Any,
    *,
    reasoning: dict[str, Any] | None = None,
    max_output_tokens: int | None = None,
    timeout: int | None = None,
) -> ProviderConfig:
    """Pinned OpenRouter/DeepSeek provider config. Never an alias, never auto-routed.

    Defaults to reasoning disabled (the first-integration-test configuration, matching Qwen Arm A
    as closely as this model allows). Pass ``reasoning``/``max_output_tokens``/``timeout``
    explicitly to build a different, still-fully-recorded configuration for a separate diagnostic
    (e.g. an explicit thinking-mode reasoning audit, which needs a much longer HTTP timeout than
    the default -- a "max"-effort trace can run tens of thousands of tokens) -- never an
    undocumented default.
    """
    return ProviderConfig(
        provider="openrouter",
        base_url=settings.openrouter_base_url,
        model=settings.openrouter_model,
        api_key=settings.openrouter_api_key,
        timeout=timeout if timeout is not None else settings.openrouter_timeout_seconds,
        extra_headers={"HTTP-Referer": OPENROUTER_REFERER, "X-OpenRouter-Title": OPENROUTER_APP_TITLE},
        reasoning=reasoning if reasoning is not None else {"enabled": False},
        max_output_tokens=max_output_tokens if max_output_tokens is not None else settings.openrouter_max_output_tokens,
    )

# Any key below appearing anywhere in the evidence packet indicates a blindness leak.
FORBIDDEN_PACKET_KEYS = {
    "comparison_price",
    "yes_price_displayed",
    "no_price_displayed",
    "yes_best_bid",
    "yes_best_ask",
    "no_best_bid",
    "no_best_ask",
    "yes_midpoint",
    "no_midpoint",
    "last_trade_price",
    "spread",
    "depth_1c",
    "depth_3c",
    "depth_5c",
    "volume",
    "liquidity",
    "executable_buy_price",
    "executable_sell_price",
    "price_type",
    "partisan_premium",
    "fair_value",
    "market_probability",
    "polymarket_probability",
    "raw_ppi",
    "comparison_price_at_join",
    "average_signed_premium",
    "median_signed_premium",
    "average_absolute_premium",
}

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


class BlindFairValueEstimate(BaseModel):
    fair_value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    should_abstain: bool = False
    rationale_short: str = Field(min_length=1, max_length=700)
    key_uncertainties: list[str] = Field(default_factory=list, max_length=5)
    base_rate_notes: str = Field(default="", max_length=500)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _isoformat_utc(value: datetime | None) -> str | None:
    """SQLite silently drops tzinfo from DateTime(timezone=True) columns across a session
    round-trip (a value built in-process is aware; the same value re-read from SQLite comes
    back naive), so .isoformat() renders differently depending on whether the object is fresh
    or reloaded. Assume UTC (matching utcnow()/parse_dt(), the only writers of these columns) so
    the prompt -- and therefore prompt_hash -- is deterministic regardless of session history."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def build_blind_evidence_packet(
    session: Session, market: Market, max_items: int = MAX_EVIDENCE_ITEMS, *, require_live_classifier: bool = False
) -> tuple[dict[str, Any], list[int]]:
    """``require_live_classifier=True`` (used by strict_llm_only runs) excludes evidence that was
    classified by anything other than a live model -- e.g. ``deterministic_fallback`` items left
    over from an earlier non-strict run, or this market's own ``ollama_failed`` rows -- even
    though both would otherwise satisfy ``relevant.is_(True)``. Evidence discovery is deduplicated
    by content hash across runs, so without this filter a canonical run could silently reuse a
    pre-existing fallback-classified item's relevance judgment instead of a live one."""
    query = select(EvidenceItem).where(EvidenceItem.market_id == market.id, EvidenceItem.relevant.is_(True))
    if require_live_classifier:
        query = query.where(EvidenceItem.classifier_provider.in_(AUTOMATED_PROVIDERS))
    items = list(
        session.scalars(
            # Postgres and SQLite disagree on NULL ordering under DESC; sort NULLs last explicitly.
            query.order_by(EvidenceItem.published_at.is_(None), EvidenceItem.published_at.desc()).limit(max_items)
        )
    )
    packet = {
        "question": market.question,
        "resolution_criteria": market.rules or market.resolution_source or "",
        "category": market.category,
        "region": market.region,
        "end_date": _isoformat_utc(market.end_date),
        "evidence": [
            {
                "title": i.title,
                "summary": (i.summary or (i.content_text or ""))[:600],
                "source_name": i.source_name,
                "published_at": _isoformat_utc(i.published_at),
                "category": i.category,
            }
            for i in items
        ],
    }
    return packet, [i.id for i in items]


def assert_blind_packet(packet: dict[str, Any]) -> None:
    """Raise if the packet contains any market-price-derived field. Defense in depth against
    a future evidence field silently leaking market consensus into the blind prompt."""

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in FORBIDDEN_PACKET_KEYS:
                    raise ValueError(f"Blind evidence packet leaked forbidden market-price field: {key}")
                _walk(value)
        elif isinstance(obj, list):
            for value in obj:
                _walk(value)

    _walk(packet)


def _all_evidence_live_classified(session: Session, evidence_ids: list[int]) -> bool:
    """True if every referenced evidence item was itself classified by a live model.

    Empty evidence (no items at all) counts as vacuously true -- there is nothing non-live to
    have contaminated the packet.
    """
    if not evidence_ids:
        return True
    providers = session.scalars(select(EvidenceItem.classifier_provider).where(EvidenceItem.id.in_(evidence_ids)))
    return all(p in AUTOMATED_PROVIDERS for p in providers)


def build_prompt(packet: dict[str, Any]) -> str:
    evidence_items = packet.get("evidence") or []
    if evidence_items:
        evidence_text = "\n\n".join(
            f"### {e.get('title')}\n{e.get('summary')}\n"
            f"(source: {e.get('source_name')}, published: {e.get('published_at')})"
            for e in evidence_items
        )
    else:
        evidence_text = (
            "No relevant evidence items were found in the database for this market. Use only the "
            "contract description and broad political base rates. Keep confidence low."
        )
    if len(evidence_text) > MAX_EVIDENCE_CHARS:
        evidence_text = evidence_text[:MAX_EVIDENCE_CHARS] + "\n\n[TRUNCATED]"
    return USER_PROMPT_TEMPLATE.format(
        question=packet.get("question", ""),
        resolution_criteria=packet.get("resolution_criteria", ""),
        category=packet.get("category", ""),
        region=packet.get("region", ""),
        end_date=packet.get("end_date", ""),
        evidence_text=evidence_text,
    )


def determine_run_slot(day: date, now: datetime, tolerance_hours: int = 2) -> str:
    """Map a generation timestamp onto one of the two canonical daily slots.

    Scheduled runs fire near ``primary_run_hour_utc``/``backup_run_hour_utc`` (see
    ``.github/workflows/ppi-daily.yml``); a run within ``tolerance_hours`` of either is treated
    as that canonical slot so the twice-daily cadence produces exactly two append-only rows per
    market per day. Anything else (manual/local runs at arbitrary times) gets its own
    timestamped adhoc slot so it never collides with -- or silently substitutes for -- a
    scheduled observation.
    """
    settings = get_settings()
    hour = now.hour
    if abs(hour - settings.primary_run_hour_utc) <= tolerance_hours:
        return f"{day.isoformat()}:primary"
    if abs(hour - settings.backup_run_hour_utc) <= tolerance_hours:
        return f"{day.isoformat()}:backup"
    return f"{day.isoformat()}:adhoc-{now.strftime('%H%M%S')}"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _call_ollama(prompt: str, base_url: str, model: str, timeout: int) -> tuple[str, str | None]:
    url = base_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": SYSTEM_INSTRUCTIONS + "\n\n" + prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": GENERATION_TEMPERATURE, "num_ctx": GENERATION_NUM_CTX},
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return str(response.json().get("response", "")), None
    except Exception as exc:  # noqa: BLE001 - persisted as an explicit failed-forecast state, not swallowed
        return "", f"{type(exc).__name__}: {exc}"


def _call_openrouter(prompt: str, config: ProviderConfig) -> tuple[str, str | None, dict[str, Any] | None]:
    """Calls OpenRouter's OpenAI-compatible chat-completions endpoint for one forecast attempt.

    Mirrors ``app.ppi.classifier.OpenAICompatibleClassifier``'s wire shape (bearer auth,
    ``/chat/completions``, ``response_format: json_object``), extended with OpenRouter's unified
    ``reasoning`` object, attribution headers, and token-usage/served-model accounting. Never
    falls back to another provider on failure -- returns an explicit error string, same
    two-outcome contract as ``_call_ollama`` plus one extra (usage) field. No API key is ever
    included in the returned error strings or logged.
    """
    if not config.api_key:
        return "", "MissingAPIKey: openrouter_api_key is not configured; no request was sent", None

    reasoning_enabled = bool(config.reasoning and config.reasoning.get("enabled"))

    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", **config.extra_headers}
    headers["Authorization"] = f"Bearer {config.api_key}"
    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ],
        "temperature": config.temperature,
    }
    # Mirrors the empirically-verified Ollama/Qwen interaction (format=json + think=true together
    # suppress thinking output on that build): defensively skip forcing response_format when
    # reasoning is requested, rather than risk the same silent suppression on this provider/model,
    # which has not been separately verified. JSON is still requested explicitly via
    # SYSTEM_INSTRUCTIONS/the prompt's own schema block, and extracted the same tolerant way
    # _extract_json_object already handles <think>-tag responses.
    if not reasoning_enabled:
        body["response_format"] = {"type": "json_object"}
    if config.reasoning is not None:
        body["reasoning"] = config.reasoning
    if config.max_output_tokens is not None:
        body["max_tokens"] = config.max_output_tokens

    try:
        response = requests.post(url, headers=headers, json=body, timeout=config.timeout)
    except requests.exceptions.Timeout as exc:
        return "", f"Timeout: {exc}", None
    except requests.exceptions.RequestException as exc:
        return "", f"{type(exc).__name__}: {exc}", None

    if response.status_code == 429:
        return "", f"RateLimited: HTTP 429 {response.text[:300]}", None
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        return "", f"HTTPError: {exc} :: {response.text[:300]}", None

    try:
        payload = response.json()
        message = payload["choices"][0]["message"]
        text = str(message["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return "", f"MalformedResponse: {type(exc).__name__}: {exc}", None

    usage = payload.get("usage") or {}
    usage_info = {
        "requested_model": config.model,
        "served_model": payload.get("model"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "reasoning": config.reasoning,
        # Populated only when reasoning was requested and the provider returned a trace --
        # OpenRouter's documented shape: message.reasoning (plaintext) and/or
        # message.reasoning_details (structured blocks). Never fabricated when absent.
        "reasoning_trace": message.get("reasoning"),
        "reasoning_details": message.get("reasoning_details"),
    }
    return text, None, usage_info


def generate_blind_forecast(
    session: Session,
    market: Market,
    *,
    job: JobRun | None,
    run_key: str,
    trigger_type: str,
    day: date,
    now: datetime | None = None,
    strict: bool = False,
    provider_config: ProviderConfig | None = None,
) -> LLMForecast:
    """Generate (or resume) the blind forecast for one market's twice-daily run slot.

    A slot already at status OK is returned unchanged -- a valid primary-model probability is
    final for that run and is never edited. A slot that previously failed or was skipped may be
    retried in place, since it never produced a valid forecast to protect.

    ``strict=True`` additionally requires every evidence item shown to the model to have itself
    been classified by a live model (see ``build_blind_evidence_packet``'s
    ``require_live_classifier``), so a canonical run can never be informed by a deterministic- or
    failed-classification judgment left over from an earlier non-strict run.

    ``provider_config`` selects the model/provider for this call. When omitted, it is built from
    global ``settings`` exactly as this function has always behaved (the primary Qwen/Ollama
    series) -- passing one explicitly is how a second, separately-versioned model series (e.g.
    OpenRouter/DeepSeek) is generated, as an independent, non-overwriting row for the same
    market/run_slot (see the ``(market_id, run_slot, model_provider)`` uniqueness below).
    """
    settings = get_settings()
    now = now or utcnow()
    run_slot = determine_run_slot(day, now)
    pc = provider_config or default_provider_config(settings)

    row = session.scalar(
        select(LLMForecast).where(
            LLMForecast.market_id == market.id,
            LLMForecast.run_slot == run_slot,
            LLMForecast.model_provider == pc.provider,
        )
    )
    if row and row.status == "OK":
        return row
    if row is None:
        row = LLMForecast(market_id=market.id, run_slot=run_slot, model_provider=pc.provider)
        session.add(row)

    row.job_run_id = job.id if job else None
    row.run_key = run_key
    row.trigger_type = trigger_type
    row.generated_at = now
    row.model_provider = pc.provider
    row.model_name = pc.model
    row.prompt_version = PROMPT_VERSION
    generation_params: dict[str, Any] = {"temperature": pc.temperature, "max_retries": MAX_RETRIES}
    if pc.provider == "ollama":
        generation_params["num_ctx"] = GENERATION_NUM_CTX
    elif pc.provider == "openrouter":
        generation_params["reasoning"] = pc.reasoning
        generation_params["max_output_tokens"] = pc.max_output_tokens
    row.generation_params_json = json.dumps(generation_params)

    packet, evidence_ids = build_blind_evidence_packet(session, market, require_live_classifier=strict)
    assert_blind_packet(packet)
    row.evidence_ids_json = json.dumps(evidence_ids)
    row.evidence_all_live_classified = _all_evidence_live_classified(session, evidence_ids)
    prompt = build_prompt(packet)
    row.prompt_hash = _stable_hash(prompt)

    if pc.provider not in AUTOMATED_PROVIDERS:
        row.status = "SKIPPED_PROVIDER"
        row.error_message = (
            f"model_provider={pc.provider!r} cannot produce an automated forecast; requires "
            "ollama, openai_compatible, or openrouter."
        )
        row.retries = 0
        row.raw_response = None
        session.flush()
        return row

    if pc.provider not in ("ollama", "openrouter"):
        # Declared in AUTOMATED_PROVIDERS (e.g. the still-unimplemented generic
        # "openai_compatible" forecast path) but no call function exists for it here yet -- fail
        # honestly and explicitly rather than silently falling through to a mismatched request
        # shape against whatever base_url happens to be configured.
        row.status = "SKIPPED_PROVIDER"
        row.error_message = (
            f"model_provider={pc.provider!r} is not yet implemented for forecast generation "
            "(currently supported: ollama, openrouter)."
        )
        row.retries = 0
        row.raw_response = None
        session.flush()
        return row

    raw_response = ""
    call_error: str | None = None
    usage_info: dict[str, Any] | None = None
    parsed: BlindFairValueEstimate | None = None
    parse_error: str | None = None
    attempts = 0

    for attempt in range(1, MAX_RETRIES + 2):
        attempts = attempt
        if pc.provider == "ollama":
            raw_response, call_error = _call_ollama(prompt, pc.base_url, pc.model, pc.timeout)
        else:
            raw_response, call_error, usage_info = _call_openrouter(prompt, pc)
        if call_error:
            parse_error = call_error
            continue
        obj = _extract_json_object(raw_response)
        if obj is None:
            parse_error = "FAILED_PARSE: no JSON object found in model output"
            continue
        try:
            parsed = BlindFairValueEstimate.model_validate(obj)
            parse_error = None
            break
        except ValidationError as exc:
            parse_error = f"ValidationError: {exc}"

    row.retries = attempts - 1
    row.raw_response = raw_response
    if usage_info is not None:
        # Cost/usage metadata is descriptive only -- merged into the same params blob, never read
        # by anything that influences fair_value/confidence/status.
        generation_params["usage"] = usage_info
        row.generation_params_json = json.dumps(generation_params)

    if parsed is None:
        row.status = "FAILED"
        row.error_message = parse_error or "Unknown failure"
        row.fair_value = None
        row.confidence = None
        row.should_abstain = None
        row.rationale = None
        row.key_uncertainties_json = None
        row.base_rate_notes = None
    else:
        row.fair_value = parsed.fair_value
        row.confidence = parsed.confidence
        row.should_abstain = parsed.should_abstain
        row.rationale = parsed.rationale_short
        row.key_uncertainties_json = json.dumps(parsed.key_uncertainties)
        row.base_rate_notes = parsed.base_rate_notes
        row.status = "ABSTAINED" if parsed.should_abstain else "OK"
        row.error_message = None

    session.flush()
    return row


def join_forecast_with_price(session: Session, forecast: LLMForecast, snapshot: MarketSnapshot | None) -> None:
    """Attach the raw PPI comparison after the forecast has already been persisted.

    ``raw_ppi = polymarket_probability - llm_fair_value`` per the canonical PPI formula. This
    must only run after ``generate_blind_forecast`` has committed the forecast row, so the model
    output can never be influenced by -- or silently substituted with -- the market price.
    """
    if forecast.status not in {"OK", "ABSTAINED"} or forecast.fair_value is None:
        return
    if snapshot is None or snapshot.comparison_price is None:
        return
    forecast.price_snapshot_id = snapshot.id
    forecast.comparison_price_at_join = snapshot.comparison_price
    forecast.raw_ppi = float(snapshot.comparison_price) - float(forecast.fair_value)
    forecast.joined_at = utcnow()
    session.flush()


def compute_and_persist_blind_index(session: Session, job: JobRun, run_key: str) -> BlindIndexRun:
    """Aggregate the primary blind-LLM series for one run_key into ``BlindIndexRun``.

    Structurally separate from ``DailyIndex`` (the legacy human-weighted series' aggregate) --
    see ``BlindIndexRun``'s docstring. Upserted by ``run_key``: rerunning the exact same scheduled
    window recomputes this summary in place from the (immutable) ``LLMForecast`` rows for that
    run rather than appending a duplicate, since the aggregate itself is a derived rollup, not a
    raw model observation.
    """
    settings = get_settings()
    forecasts = list(
        session.scalars(
            select(LLMForecast).where(
                LLMForecast.run_key == run_key,
                LLMForecast.raw_ppi.is_not(None),
                LLMForecast.model_provider.in_(PRIMARY_SERIES_PROVIDERS),
            )
        )
    )
    premiums = [f.raw_ppi for f in forecasts if f.raw_ppi is not None]

    row = session.scalar(select(BlindIndexRun).where(BlindIndexRun.run_key == run_key))
    if not row:
        row = BlindIndexRun(run_key=run_key)
        session.add(row)
    row.job_run_id = job.id
    row.effective_timestamp = utcnow()
    row.market_count = len(premiums)
    row.average_signed_premium = (sum(premiums) / len(premiums)) if premiums else None
    row.median_signed_premium = median(premiums) if premiums else None
    row.average_absolute_premium = (sum(abs(p) for p in premiums) / len(premiums)) if premiums else None
    row.model_name = settings.llm_model or settings.ollama_model
    row.prompt_version = PROMPT_VERSION
    session.flush()
    return row
