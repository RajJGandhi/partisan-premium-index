#!/usr/bin/env python3
"""
scripts/run_llm_fair_values.py

Blind LLM fair-value runner for Reality Spread.

Input:
    data/signal_inputs/signal_input_latest.csv

Outputs:
    data/llm_estimates/llm_estimates_latest.csv
    data/llm_estimates/llm_estimates_<run_id>.csv
    data/snapshots/llm_estimate_snapshots.csv
    data/health/latest_llm_estimate_health.json
    data/health/llm_estimate_health_<run_id>.json

Default model backend:
    Ollama local API at http://localhost:11434

Recommended model:
    qwen3:8b or llama3.1:8b or mistral-nemo depending on what you have installed.

Important:
    This script intentionally does NOT include comparison_price, best_bid, best_ask,
    spread, or market odds in the LLM prompt. The model gives a blind fair value.

Before running real estimates:
    ollama pull qwen3:8b
    ollama serve

Test without Ollama:
    PYTHONPATH=. python scripts/run_llm_fair_values.py --mock --limit 5

Run small real test:
    PYTHONPATH=. python scripts/run_llm_fair_values.py \
      --model qwen3:8b \
      --limit 5

Run full:
    PYTHONPATH=. python scripts/run_llm_fair_values.py \
      --model qwen3:8b
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


PROMPT_VERSION = "fair_value_v0.1"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

OUTPUT_COLUMNS = [
    "estimate_id",
    "run_id",
    "timestamp_utc",
    "prompt_version",
    "model_backend",
    "model_name",
    "tracking_id",
    "parent_market_name",
    "market_name",
    "primary_outcome_to_track",
    "region",
    "bucket",
    "system_type",
    "underlying_event_group",
    "gamma_market_id",
    "condition_id",
    "exact_polymarket_slug",
    "market_url",
    "token_id",
    "outcome_contract_question",
    "fair_value",
    "confidence",
    "should_abstain",
    "rationale_short",
    "key_uncertainties_json",
    "base_rate_notes",
    "evidence_packet_paths_json",
    "prompt_hash",
    "raw_response",
    "parse_status",
    "error",
    # Market fields copied only AFTER estimate, for downstream join/debug.
    # These are not included in the LLM prompt.
    "comparison_price",
    "price_type",
    "liquidity_flags",
    "signal_ready",
]


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
- Parent market: {parent_market_name}
- Specific outcome being estimated: {primary_outcome_to_track}
- Contract question: {outcome_contract_question}
- Region: {region}
- Bucket: {bucket}
- System type: {system_type}
- Underlying event group: {underlying_event_group}

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


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(ts: Optional[dt.datetime] = None) -> str:
    return (ts or utc_now()).isoformat().replace("+00:00", "Z")


def slugify(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except Exception:
        return None


def clamp_probability(value: Any) -> Optional[float]:
    val = safe_float(value)
    if val is None:
        return None
    if val < 0:
        return 0.0
    if val > 1:
        return 1.0
    return round(val, 6)


def read_csv(path: Path, limit: Optional[int] = None) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if limit is not None:
        rows = rows[:limit]
    return rows


def write_csv(path: Path, rows: List[Dict[str, str]], fieldnames: Sequence[str], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def read_text_if_exists(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def evidence_paths_for_row(row: Dict[str, str], evidence_root: Path) -> List[Path]:
    region = row.get("region", "")
    bucket = row.get("bucket", "")
    system_type = row.get("system_type", "")
    parent = row.get("parent_market_name", "")
    event_group = row.get("underlying_event_group", "")
    tracking_id = row.get("tracking_id", "")

    paths: List[Path] = []

    # Shared context by broad region.
    if region == "US":
        paths.append(evidence_root / "shared" / "us_midterms_context.md")
    elif region == "Brazil":
        paths.append(evidence_root / "shared" / "brazil_context.md")
    elif region:
        paths.append(evidence_root / "shared" / "global_satellite_context.md")

    # Shared context by bucket/system.
    if bucket:
        paths.append(evidence_root / "shared" / f"{slugify(bucket)}.md")
    if system_type:
        paths.append(evidence_root / "shared" / f"{slugify(system_type)}.md")

    # Parent/event-level context.
    if event_group:
        paths.append(evidence_root / "parents" / f"{slugify(event_group)}.md")
    if parent:
        paths.append(evidence_root / "parents" / f"{slugify(parent)}.md")

    # Row-specific context.
    if tracking_id:
        paths.append(evidence_root / "markets" / f"{tracking_id}.md")

    # De-dupe.
    out: List[Path] = []
    seen = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def load_evidence_for_row(row: Dict[str, str], evidence_root: Path, max_chars: int) -> Tuple[str, List[str]]:
    chunks: List[str] = []
    used_paths: List[str] = []

    for path in evidence_paths_for_row(row, evidence_root):
        text = read_text_if_exists(path)
        if not text:
            continue
        used_paths.append(str(path))
        chunks.append(f"### {path}\n{text}")

    if not chunks:
        fallback = (
            "No external evidence packet was found for this row. Use only the contract description, "
            "broad political base rates, and the uncertainty implied by the market type. Keep confidence low."
        )
        return fallback, []

    evidence_text = "\n\n".join(chunks)
    if len(evidence_text) > max_chars:
        evidence_text = evidence_text[:max_chars] + "\n\n[TRUNCATED]"
    return evidence_text, used_paths


def build_prompt(row: Dict[str, str], evidence_text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(
        parent_market_name=row.get("parent_market_name", ""),
        primary_outcome_to_track=row.get("primary_outcome_to_track", ""),
        outcome_contract_question=row.get("outcome_contract_question", ""),
        region=row.get("region", ""),
        bucket=row.get("bucket", ""),
        system_type=row.get("system_type", ""),
        underlying_event_group=row.get("underlying_event_group", ""),
        evidence_text=evidence_text,
    )


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    # Remove common thinking tags from reasoning models.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # Find first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def call_ollama(
    prompt: str,
    model: str,
    ollama_url: str,
    temperature: float,
    num_ctx: int,
    timeout: int,
) -> Tuple[str, Optional[str]]:
    url = ollama_url.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": SYSTEM_INSTRUCTIONS + "\n\n" + prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
        },
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "")), None
    except Exception as exc:
        return "", str(exc)


def mock_response_for_row(row: Dict[str, str]) -> Dict[str, Any]:
    """
    Deterministic-ish mock for pipeline testing. Not for research.
    """
    key = row.get("tracking_id", "") + row.get("primary_outcome_to_track", "")
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)
    fair = 0.02 + (h % 9300) / 10000
    fair = max(0.001, min(0.999, fair))
    return {
        "fair_value": round(fair, 4),
        "confidence": 0.25,
        "should_abstain": True,
        "rationale_short": "MOCK estimate for pipeline testing only.",
        "key_uncertainties": ["Mock mode", "No real evidence used"],
        "base_rate_notes": "Mock mode; do not use analytically.",
    }


def normalize_estimate_json(obj: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    fair_value = clamp_probability(obj.get("fair_value"))
    confidence = clamp_probability(obj.get("confidence"))

    should_abstain = obj.get("should_abstain", False)
    if isinstance(should_abstain, str):
        should_abstain = should_abstain.strip().lower() in {"true", "yes", "1"}

    rationale_short = str(obj.get("rationale_short", "")).strip()
    base_rate_notes = str(obj.get("base_rate_notes", "")).strip()

    key_uncertainties = obj.get("key_uncertainties", [])
    if isinstance(key_uncertainties, str):
        key_uncertainties = [key_uncertainties]
    if not isinstance(key_uncertainties, list):
        key_uncertainties = []
    key_uncertainties = [str(x).strip() for x in key_uncertainties if str(x).strip()][:5]

    errors = []
    if fair_value is None:
        errors.append("missing_or_invalid_fair_value")
    if confidence is None:
        errors.append("missing_or_invalid_confidence")
    if not rationale_short:
        errors.append("missing_rationale_short")
    if not key_uncertainties:
        errors.append("missing_key_uncertainties")

    normalized = {
        "fair_value": fair_value,
        "confidence": confidence,
        "should_abstain": bool(should_abstain),
        "rationale_short": rationale_short[:700],
        "key_uncertainties": key_uncertainties,
        "base_rate_notes": base_rate_notes[:500],
    }

    parse_status = "OK" if not errors else "INVALID:" + "|".join(errors)
    return normalized, parse_status


def estimate_row(
    row: Dict[str, str],
    *,
    run_id: str,
    timestamp_utc: str,
    model_backend: str,
    model: str,
    ollama_url: str,
    evidence_root: Path,
    max_evidence_chars: int,
    temperature: float,
    num_ctx: int,
    timeout: int,
    retries: int,
    mock: bool,
) -> Dict[str, str]:
    evidence_text, evidence_paths = load_evidence_for_row(row, evidence_root, max_chars=max_evidence_chars)
    prompt = build_prompt(row, evidence_text)
    prompt_hash = stable_hash(prompt)

    raw_response = ""
    error = ""
    estimate_obj: Optional[Dict[str, Any]] = None

    if mock:
        estimate_obj = mock_response_for_row(row)
        raw_response = json.dumps(estimate_obj, ensure_ascii=False)
    else:
        for attempt in range(1, retries + 1):
            raw_response, error = call_ollama(
                prompt=prompt,
                model=model,
                ollama_url=ollama_url,
                temperature=temperature,
                num_ctx=num_ctx,
                timeout=timeout,
            )
            estimate_obj = extract_json_object(raw_response)
            if estimate_obj is not None:
                break
            if attempt < retries:
                time.sleep(1.0 * attempt)

    if estimate_obj is None:
        normalized = {
            "fair_value": None,
            "confidence": None,
            "should_abstain": True,
            "rationale_short": "",
            "key_uncertainties": [],
            "base_rate_notes": "",
        }
        parse_status = "FAILED_PARSE"
    else:
        normalized, parse_status = normalize_estimate_json(estimate_obj)

    if parse_status != "OK" and not error:
        error = parse_status

    out = {col: "" for col in OUTPUT_COLUMNS}
    out.update(
        {
            "estimate_id": f"{run_id}:{row.get('tracking_id', '')}",
            "run_id": run_id,
            "timestamp_utc": timestamp_utc,
            "prompt_version": PROMPT_VERSION,
            "model_backend": model_backend,
            "model_name": model,
            "tracking_id": row.get("tracking_id", ""),
            "parent_market_name": row.get("parent_market_name", ""),
            "market_name": row.get("market_name", ""),
            "primary_outcome_to_track": row.get("primary_outcome_to_track", ""),
            "region": row.get("region", ""),
            "bucket": row.get("bucket", ""),
            "system_type": row.get("system_type", ""),
            "underlying_event_group": row.get("underlying_event_group", ""),
            "gamma_market_id": row.get("gamma_market_id", ""),
            "condition_id": row.get("condition_id", ""),
            "exact_polymarket_slug": row.get("exact_polymarket_slug", ""),
            "market_url": row.get("market_url", ""),
            "token_id": row.get("token_id", ""),
            "outcome_contract_question": row.get("outcome_contract_question", ""),
            "fair_value": "" if normalized["fair_value"] is None else str(normalized["fair_value"]),
            "confidence": "" if normalized["confidence"] is None else str(normalized["confidence"]),
            "should_abstain": str(normalized["should_abstain"]).lower(),
            "rationale_short": normalized["rationale_short"],
            "key_uncertainties_json": json.dumps(normalized["key_uncertainties"], ensure_ascii=False),
            "base_rate_notes": normalized["base_rate_notes"],
            "evidence_packet_paths_json": json.dumps(evidence_paths, ensure_ascii=False),
            "prompt_hash": prompt_hash,
            "raw_response": raw_response,
            "parse_status": parse_status,
            "error": error,
            "comparison_price": row.get("comparison_price", ""),
            "price_type": row.get("price_type", ""),
            "liquidity_flags": row.get("liquidity_flags", ""),
            "signal_ready": row.get("signal_ready", ""),
        }
    )
    return out


def write_health_report(path: Path, latest_path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def build_health_report(
    estimates: List[Dict[str, str]],
    *,
    run_id: str,
    timestamp_utc: str,
    input_path: str,
    latest_output_path: str,
    snapshot_output_path: str,
    append_output_path: str,
    model_backend: str,
    model_name: str,
    mock: bool,
) -> Dict[str, Any]:
    parse_counts: Dict[str, int] = {}
    abstain_count = 0
    fair_values: List[float] = []
    confidence_values: List[float] = []
    missing_evidence_count = 0

    for row in estimates:
        parse_counts[row["parse_status"]] = parse_counts.get(row["parse_status"], 0) + 1
        if row.get("should_abstain") == "true":
            abstain_count += 1

        fv = safe_float(row.get("fair_value"))
        conf = safe_float(row.get("confidence"))
        if fv is not None:
            fair_values.append(fv)
        if conf is not None:
            confidence_values.append(conf)

        paths = row.get("evidence_packet_paths_json", "[]")
        try:
            parsed = json.loads(paths)
            if not parsed:
                missing_evidence_count += 1
        except Exception:
            missing_evidence_count += 1

    ok_count = parse_counts.get("OK", 0)

    return {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "prompt_version": PROMPT_VERSION,
        "input_path": input_path,
        "latest_output_path": latest_output_path,
        "snapshot_output_path": snapshot_output_path,
        "append_output_path": append_output_path,
        "model_backend": model_backend,
        "model_name": model_name,
        "mock": mock,
        "rows_total": len(estimates),
        "parse_status_counts": parse_counts,
        "ok_count": ok_count,
        "ok_rate": round(ok_count / max(1, len(estimates)), 4),
        "abstain_count": abstain_count,
        "missing_evidence_count": missing_evidence_count,
        "avg_fair_value": round(sum(fair_values) / len(fair_values), 6) if fair_values else None,
        "min_fair_value": round(min(fair_values), 6) if fair_values else None,
        "max_fair_value": round(max(fair_values), 6) if fair_values else None,
        "avg_confidence": round(sum(confidence_values) / len(confidence_values), 6) if confidence_values else None,
        "sample_errors": [
            {
                "tracking_id": r.get("tracking_id"),
                "parent_market_name": r.get("parent_market_name"),
                "outcome": r.get("primary_outcome_to_track"),
                "parse_status": r.get("parse_status"),
                "error": r.get("error"),
                "raw_response": r.get("raw_response", "")[:500],
            }
            for r in estimates
            if r.get("parse_status") != "OK"
        ][:25],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run blind LLM fair-value estimates for signal-ready market rows.")
    parser.add_argument("--input", default="data/signal_inputs/signal_input_latest.csv")
    parser.add_argument("--latest-output", default="data/llm_estimates/llm_estimates_latest.csv")
    parser.add_argument("--snapshot-dir", default="data/llm_estimates")
    parser.add_argument("--append-output", default="data/snapshots/llm_estimate_snapshots.csv")
    parser.add_argument("--health-dir", default="data/health")
    parser.add_argument("--evidence-root", default="evidence")
    parser.add_argument("--model-backend", default="ollama", choices=["ollama", "mock"])
    parser.add_argument("--model", default=os.getenv("REALITY_SPREAD_LLM_MODEL", "qwen3:8b"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only-signal-ready", action="store_true", default=True)
    parser.add_argument("--temperature", type=float, default=0.15)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-evidence-chars", type=int, default=9000)
    parser.add_argument("--mock", action="store_true", help="Use deterministic mock estimates for pipeline testing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    timestamp = utc_now()
    timestamp_utc = iso_utc(timestamp)
    run_id = timestamp.strftime("%Y%m%dT%H%M%SZ")

    rows = read_csv(Path(args.input), limit=args.limit)
    if args.only_signal_ready:
        rows = [r for r in rows if r.get("signal_ready", "").lower() == "true"]

    mock = args.mock or args.model_backend == "mock"
    model_backend = "mock" if mock else args.model_backend

    print(f"Run ID: {run_id}")
    print(f"Input rows: {len(rows)}")
    print(f"Model backend: {model_backend}")
    print(f"Model: {args.model}")
    print(f"Evidence root: {args.evidence_root}")

    estimates: List[Dict[str, str]] = []

    for i, row in enumerate(rows, start=1):
        print(
            f"[{i}/{len(rows)}] {row.get('tracking_id')} | {row.get('parent_market_name')} — {row.get('primary_outcome_to_track')}"
        )
        estimate = estimate_row(
            row,
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            model_backend=model_backend,
            model=args.model,
            ollama_url=args.ollama_url,
            evidence_root=Path(args.evidence_root),
            max_evidence_chars=args.max_evidence_chars,
            temperature=args.temperature,
            num_ctx=args.num_ctx,
            timeout=args.timeout,
            retries=args.retries,
            mock=mock,
        )
        estimates.append(estimate)

        # Write latest output incrementally for crash recovery.
        write_csv(Path(args.latest_output), estimates, OUTPUT_COLUMNS, append=False)

    snapshot_output = Path(args.snapshot_dir) / f"llm_estimates_{run_id}.csv"
    append_output = Path(args.append_output)

    write_csv(Path(args.latest_output), estimates, OUTPUT_COLUMNS, append=False)
    write_csv(snapshot_output, estimates, OUTPUT_COLUMNS, append=False)
    write_csv(append_output, estimates, OUTPUT_COLUMNS, append=True)

    health_report = build_health_report(
        estimates,
        run_id=run_id,
        timestamp_utc=timestamp_utc,
        input_path=args.input,
        latest_output_path=args.latest_output,
        snapshot_output_path=str(snapshot_output),
        append_output_path=str(append_output),
        model_backend=model_backend,
        model_name=args.model,
        mock=mock,
    )

    health_path = Path(args.health_dir) / f"llm_estimate_health_{run_id}.json"
    latest_health_path = Path(args.health_dir) / "latest_llm_estimate_health.json"
    write_health_report(health_path, latest_health_path, health_report)

    print("\nDone.")
    print(f"Rows: {health_report['rows_total']}")
    print(f"OK: {health_report['ok_count']}")
    print(f"OK rate: {health_report['ok_rate']}")
    print(f"Abstain count: {health_report['abstain_count']}")
    print(f"Missing evidence count: {health_report['missing_evidence_count']}")
    print(f"Avg fair value: {health_report['avg_fair_value']}")
    print(f"Avg confidence: {health_report['avg_confidence']}")

    print("\nWrote:")
    print(f"  Latest estimates: {args.latest_output}")
    print(f"  Timestamped estimates: {snapshot_output}")
    print(f"  Append-only estimates: {append_output}")
    print(f"  Latest health: {latest_health_path}")
    print(f"  Timestamped health: {health_path}")

    if health_report["ok_count"] == 0:
        raise SystemExit("No valid LLM estimates produced.")


if __name__ == "__main__":
    main()
