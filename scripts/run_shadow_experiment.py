"""Noncanonical shadow experiment: replay a frozen historical evidence packet under multiple
generation configurations to characterize a probability-clustering anomaly (see
docs/OPERATIONS.md "Shadow experiments").

This never touches the production database. It reads a frozen-inputs JSON file (produced by
`scripts/audit_llm_forecast_run.py`'s enriched per-forecast `evidence_items`/`market_*` fields --
the exact evidence and market metadata a real historical run actually used) and writes a plain
JSON results file. No `DATABASE_URL`, no `LLMForecast` row, no `JobRun` row, no
`app.ppi.pipeline`/`app.ppi.blind_forecast.generate_blind_forecast` call, no publication path is
ever touched -- these are diagnostic-only generations, never mistakable for a canonical PPI
observation.

Reuses the real production prompt/schema/blindness-check/JSON-extraction code from
app.ppi.blind_forecast wherever an arm is meant to match production exactly, so "Arm A" is a
genuine replication, not a reimplementation that could silently drift from what production does.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from pydantic import BaseModel, Field, ValidationError

from app.ppi.blind_forecast import (
    GENERATION_NUM_CTX,
    GENERATION_TEMPERATURE,
    MAX_RETRIES,
    SYSTEM_INSTRUCTIONS,
    BlindFairValueEstimate,
    _extract_json_object,
    assert_blind_packet,
    build_prompt,
)

ARMS = ("A", "B", "C", "D", "E")


class DecomposedProbabilityEstimate(BaseModel):
    """Arm D's schema: forces explicit decomposition instead of a single leap to a number.
    Deliberately does not instruct the model to avoid round numbers -- the point is to test
    whether structure alone changes the distribution, not to add another explicit anchor/nudge."""

    base_rate: str = Field(min_length=1, max_length=500)
    market_specific_evidence: str = Field(min_length=1, max_length=800)
    evidence_against: str = Field(min_length=1, max_length=800)
    uncertainty: str = Field(min_length=1, max_length=500)
    fair_value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


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


def build_decomposed_prompt(packet: dict[str, Any]) -> str:
    evidence_items = packet.get("evidence") or []
    if evidence_items:
        evidence_text = "\n\n".join(
            f"### {e.get('title')}\n{e.get('summary')}\n(source: {e.get('source_name')}, published: {e.get('published_at')})"
            for e in evidence_items
        )
    else:
        evidence_text = (
            "No relevant evidence items were found in the database for this market. Use only the "
            "contract description and broad political base rates. Keep confidence low."
        )
    return DECOMPOSED_PROMPT_TEMPLATE.format(
        question=packet.get("question", ""),
        resolution_criteria=packet.get("resolution_criteria", ""),
        category=packet.get("category", ""),
        region=packet.get("region", ""),
        end_date=packet.get("end_date", ""),
        evidence_text=evidence_text,
    )


def reconstruct_frozen_packet(forecast_row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the exact blind evidence packet a historical forecast used, from an
    audit_llm_forecast_run.py report row -- same shape as
    app.ppi.blind_forecast.build_blind_evidence_packet's own packet dict."""
    return {
        "question": forecast_row["market_question"],
        "resolution_criteria": forecast_row.get("market_resolution_criteria") or "",
        "category": forecast_row.get("market_category"),
        "region": forecast_row.get("market_region"),
        "end_date": forecast_row.get("market_end_date"),
        "evidence": [
            {
                "title": item.get("title"),
                "summary": item.get("summary"),
                "source_name": item.get("source_name"),
                "published_at": item.get("published_at"),
                "category": item.get("category"),
            }
            for item in forecast_row.get("evidence_items", [])
        ],
    }


@dataclass(frozen=True)
class ArmConfig:
    arm: str
    description: str
    prompt_builder: Callable[[dict[str, Any]], str]
    format_json: bool
    think: bool | None  # None = omit the field entirely (matches production's Arm A exactly)
    options: dict[str, Any]
    schema: type[BaseModel]


def _arm_configs() -> dict[str, ArmConfig]:
    return {
        "A": ArmConfig(
            arm="A",
            description="Production replication: exact prompt, exact generation settings.",
            prompt_builder=build_prompt,
            format_json=True,
            think=None,
            options={"temperature": GENERATION_TEMPERATURE, "num_ctx": GENERATION_NUM_CTX},
            schema=BlindFairValueEstimate,
        ),
        "B": ArmConfig(
            arm="B",
            description=(
                "Thinking enabled, Qwen3's official thinking-mode sampling "
                "(temperature=0.6, top_p=0.95, top_k=20, min_p=0). format=json is deliberately NOT "
                "set: verified empirically on this Ollama build that format=json + think=true "
                "together suppress thinking output entirely (no 'thinking' field returned) -- so "
                "JSON is extracted from free-form response text instead, the same way "
                "app.ppi.blind_forecast._extract_json_object already handles legacy <think>-tag "
                "responses."
            ),
            prompt_builder=build_prompt,
            format_json=False,
            think=True,
            options={"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0, "num_ctx": 8192},
            schema=BlindFairValueEstimate,
        ),
        "C": ArmConfig(
            arm="C",
            description=(
                "Thinking explicitly disabled, Qwen3's official non-thinking sampling "
                "(temperature=0.7, top_p=0.8, top_k=20, min_p=0)."
            ),
            prompt_builder=build_prompt,
            format_json=True,
            think=False,
            options={"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "num_ctx": GENERATION_NUM_CTX},
            schema=BlindFairValueEstimate,
        ),
        "D": ArmConfig(
            arm="D",
            description=(
                "Same evidence/model/settings as Arm A; only the prompt changes, requiring explicit "
                "decomposition (base rate / market-specific evidence / evidence against / "
                "uncertainty / final probability). No 'avoid round numbers' instruction added."
            ),
            prompt_builder=build_decomposed_prompt,
            format_json=True,
            think=None,
            options={"temperature": GENERATION_TEMPERATURE, "num_ctx": GENERATION_NUM_CTX},
            schema=DecomposedProbabilityEstimate,
        ),
        "E": ArmConfig(
            arm="E",
            description=(
                "Diagnostic only: V2 decomposition prompt/schema (same as Arm D) combined with "
                "Arm B's Qwen3 official thinking-mode sampling (temperature=0.6, top_p=0.95, "
                "top_k=20, min_p=0). Isolates whether quantization is prompt-induced (compare vs. "
                "Arm D, same settings, different prompt) or temperature/decoding-induced (compare "
                "vs. Arm D, same prompt, different settings). format=json is deliberately NOT set, "
                "matching Arm B's empirically-verified reasoning (format=json + think=true "
                "together suppress thinking on this Ollama build)."
            ),
            prompt_builder=build_decomposed_prompt,
            format_json=False,
            think=True,
            options={"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0, "num_ctx": 8192},
            schema=DecomposedProbabilityEstimate,
        ),
    }


def _call_ollama_raw(
    prompt: str,
    *,
    base_url: str,
    model: str,
    timeout: int,
    format_json: bool,
    think: bool | None,
    options: dict[str, Any],
    include_system_instructions: bool,
) -> tuple[str, str | None, str | None]:
    """Returns (response_text, thinking_text_or_None, error_or_None). Never raises for a normal
    HTTP/model failure -- that's recorded as an explicit error string, matching
    app.ppi.blind_forecast._call_ollama's own contract."""
    full_prompt = f"{SYSTEM_INSTRUCTIONS}\n\n{prompt}" if include_system_instructions else prompt
    payload: dict[str, Any] = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
        "options": options,
    }
    if format_json:
        payload["format"] = "json"
    if think is not None:
        payload["think"] = think
    try:
        response = requests.post(f"{base_url.rstrip('/')}/api/generate", json=payload, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        return str(body.get("response", "")), body.get("thinking"), None
    except Exception as exc:  # noqa: BLE001 - recorded as an explicit failure, never swallowed
        return "", None, f"{type(exc).__name__}: {exc}"


@dataclass
class GenerationResult:
    arm: str
    market_id: int
    market_slug: str | None
    repetition: int
    attempts: int
    status: str  # OK, ABSTAINED (arm A/B/C only), FAILED
    fair_value: float | None
    confidence: float | None
    parsed_fields: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    thinking: str | None = None
    error_message: str | None = None
    prompt_used: str = ""
    request_options: dict[str, Any] = field(default_factory=dict)
    format_json: bool = True
    think: bool | None = None
    generated_at: str = ""


def generate_one(
    packet: dict[str, Any],
    market_id: int,
    market_slug: str | None,
    repetition: int,
    config: ArmConfig,
    *,
    base_url: str,
    model: str,
    timeout: int,
) -> GenerationResult:
    assert_blind_packet(packet)  # same defense-in-depth check production uses before every call
    prompt = config.prompt_builder(packet)
    # Every arm keeps the same system-level forecaster framing (calibrated/disciplined/base-rate
    # guidance) for a controlled comparison -- only the arm-specific variable (sampling settings,
    # think flag, or prompt decomposition) differs from Arm A. Arm B's format=json is off, so
    # SYSTEM_INSTRUCTIONS' "Return ONLY valid JSON" line is the only thing asking for JSON there;
    # still included so the model has the same instruction, even though it isn't mechanically
    # enforced by the API for that arm.
    include_system = True

    last_error: str | None = None
    raw_response = ""
    thinking: str | None = None
    parsed: BaseModel | None = None
    attempts = 0

    for attempt in range(1, MAX_RETRIES + 2):
        attempts = attempt
        raw_response, thinking, call_error = _call_ollama_raw(
            prompt,
            base_url=base_url,
            model=model,
            timeout=timeout,
            format_json=config.format_json,
            think=config.think,
            options=config.options,
            include_system_instructions=include_system,
        )
        if call_error:
            last_error = call_error
            continue
        obj = _extract_json_object(raw_response)
        if obj is None:
            last_error = "FAILED_PARSE: no JSON object found in model output"
            continue
        try:
            parsed = config.schema.model_validate(obj)
            last_error = None
            break
        except ValidationError as exc:
            last_error = f"ValidationError: {exc}"

    generated_at = datetime.now(timezone.utc).isoformat()
    if parsed is None:
        return GenerationResult(
            arm=config.arm,
            market_id=market_id,
            market_slug=market_slug,
            repetition=repetition,
            attempts=attempts,
            status="FAILED",
            fair_value=None,
            confidence=None,
            raw_response=raw_response,
            thinking=thinking,
            error_message=last_error,
            prompt_used=prompt,
            request_options=config.options,
            format_json=config.format_json,
            think=config.think,
            generated_at=generated_at,
        )

    parsed_dict = parsed.model_dump()
    should_abstain = bool(parsed_dict.get("should_abstain")) if isinstance(parsed, BlindFairValueEstimate) else False
    return GenerationResult(
        arm=config.arm,
        market_id=market_id,
        market_slug=market_slug,
        repetition=repetition,
        attempts=attempts,
        status="ABSTAINED" if should_abstain else "OK",
        fair_value=parsed_dict.get("fair_value"),
        confidence=parsed_dict.get("confidence"),
        parsed_fields=parsed_dict,
        raw_response=raw_response,
        thinking=thinking,
        prompt_used=prompt,
        request_options=config.options,
        format_json=config.format_json,
        think=config.think,
        generated_at=generated_at,
    )


def run_experiment(
    frozen_inputs: list[dict[str, Any]],
    *,
    arms: tuple[str, ...] = ARMS,
    repetitions: int = 5,
    base_url: str,
    model: str,
    timeout: int,
    output_path: Path,
    sleep_between_calls: float = 0.0,
) -> dict[str, Any]:
    """Runs every (arm, market, repetition) generation and writes incrementally to output_path so
    a long run's partial progress survives an interruption. Never touches the database."""
    configs = _arm_configs()
    results: list[dict[str, Any]] = []
    total = len(arms) * len(frozen_inputs) * repetitions
    done = 0

    for arm in arms:
        config = configs[arm]
        for row in frozen_inputs:
            packet = reconstruct_frozen_packet(row)
            for repetition in range(1, repetitions + 1):
                result = generate_one(
                    packet,
                    row["market_id"],
                    row.get("market_slug"),
                    repetition,
                    config,
                    base_url=base_url,
                    model=model,
                    timeout=timeout,
                )
                results.append(asdict(result))
                done += 1
                print(
                    f"[{done}/{total}] arm={arm} market={row.get('market_slug')} rep={repetition} "
                    f"status={result.status} fair_value={result.fair_value}"
                )
                output_path.write_text(
                    json.dumps({"arms": {a: configs[a].description for a in arms}, "results": results}, indent=2)
                )
                if sleep_between_calls:
                    time.sleep(sleep_between_calls)

    return {"arms": {a: configs[a].description for a in arms}, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the noncanonical 4-arm shadow experiment (never writes to the DB)")
    parser.add_argument("--frozen-inputs", type=Path, required=True, help="audit_llm_forecast_run.py JSON report")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", type=str, default=",".join(ARMS))
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:11434")
    parser.add_argument("--model", type=str, default="qwen3:8b")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--sleep-between-calls", type=float, default=0.0)
    args = parser.parse_args()

    report = json.loads(args.frozen_inputs.read_text())
    frozen_inputs = report["forecasts"] if "forecasts" in report else report

    run_experiment(
        frozen_inputs,
        arms=tuple(args.arms.split(",")),
        repetitions=args.repetitions,
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
        output_path=args.output,
        sleep_between_calls=args.sleep_between_calls,
    )


if __name__ == "__main__":
    main()
