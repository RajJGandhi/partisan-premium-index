#!/usr/bin/env python3
"""
scripts/run_llm_group_fair_values.py

Group-level blind fair-value runner for multi-option markets.

Why this exists:
    Row-by-row LLM estimates are often incoherent for mutually exclusive option sets.
    Example: seat-range buckets can sum to 4.00 instead of 1.00.

This script asks the model to allocate probability mass across all options in a parent group
in one prompt.

Input:
    data/signal_inputs/signal_input_latest.csv

Outputs:
    data/llm_group_estimates/llm_group_estimates_latest.csv
    data/llm_group_estimates/llm_group_estimates_<run_id>.csv
    data/snapshots/llm_group_estimate_snapshots.csv
    data/health/latest_llm_group_estimate_health.json

Run mock:
    PYTHONPATH=. python scripts/run_llm_group_fair_values.py --mock --limit-groups 2

Run real:
    PYTHONPATH=. python scripts/run_llm_group_fair_values.py --model qwen3:8b

Notes:
    - The prompt is blind: no market price, bid, ask, spread, volume, or liquidity.
    - Each group response is normalized to sum to 1 for analysis.
    - Raw group allocation sum is preserved for diagnostics.
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


PROMPT_VERSION = "group_fair_value_v0.1"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

OUTPUT_COLUMNS = [
    "group_estimate_id",
    "run_id",
    "timestamp_utc",
    "prompt_version",
    "model_backend",
    "model_name",
    "parent_market_name",
    "group_key",
    "group_n",
    "tracking_id",
    "primary_outcome_to_track",
    "outcome_contract_question",
    "region",
    "bucket",
    "system_type",
    "underlying_event_group",
    "gamma_market_id",
    "condition_id",
    "exact_polymarket_slug",
    "market_url",
    "token_id",
    "group_fair_value_raw",
    "group_fair_value_norm",
    "group_confidence",
    "outcome_rationale_short",
    "group_rationale_short",
    "key_uncertainties_json",
    "allocation_sum_raw",
    "normalization_applied",
    "evidence_packet_paths_json",
    "prompt_hash",
    "raw_response",
    "parse_status",
    "error",
    # copied for downstream joins; not included in prompt
    "comparison_price",
    "price_type",
    "liquidity_flags",
    "signal_ready",
]

SYSTEM_INSTRUCTIONS = """You are a calibrated election and prediction-market research assistant.

Your job is to allocate probability mass across a mutually exclusive set of option-level event contracts.

You are NOT seeing current market prices. Do not infer or invent market odds.

Think like a disciplined forecaster:
- Use base rates.
- Allocate probabilities across all options so they sum to approximately 1.00.
- Be conservative under uncertainty.
- Avoid giving many options the same high probability.
- If the option set is incomplete, say so and still allocate across the listed options as conditional on the listed set.

Return ONLY valid JSON. No markdown. No commentary outside JSON.
"""

USER_PROMPT_TEMPLATE = """Estimate a blind probability distribution across this parent market's options.

PARENT MARKET:
- Parent market: {parent_market_name}
- Region: {region}
- Bucket: {bucket}
- System type: {system_type}
- Underlying event group: {underlying_event_group}

OPTIONS:
{options_text}

IMPORTANT BLINDNESS RULE:
You are not given market prices, bids, asks, spreads, volumes, liquidity, or order-book data. Do not guess the market price. Estimate your own fair probabilities only.

IMPORTANT COHERENCE RULE:
The option probabilities should sum to approximately 1.00 across the listed options. If the listed option set is incomplete, still allocate probabilities conditionally across the listed options and mention incompleteness in the rationale.

EVIDENCE PACKET:
{evidence_text}

OUTPUT JSON SCHEMA:
{{
  "group_confidence": number between 0 and 1,
  "group_rationale_short": string, max 700 characters,
  "key_uncertainties": array of 1 to 6 short strings,
  "allocations": [
    {{
      "tracking_id": "exact tracking_id from options",
      "fair_value": number between 0 and 1,
      "rationale_short": "short outcome-specific rationale"
    }}
  ]
}}

Calibration guidance:
- Total probability across allocations should be close to 1.00.
- Avoid 0 or 1 unless the outcome is essentially certain.
- Low-probability long shots should often be 0.001 to 0.05, not 0.25.
- For binary markets, use probabilities that sum to 1.00.
- For seat ranges or candidate fields, distribute probability mass across all listed buckets/candidates.
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
    return round(min(1.0, max(0.0, val)), 8)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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
    if path.exists() and path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def evidence_paths_for_group(group_rows: List[Dict[str, str]], evidence_root: Path) -> List[Path]:
    first = group_rows[0]
    region = first.get("region", "")
    bucket = first.get("bucket", "")
    system_type = first.get("system_type", "")
    parent = first.get("parent_market_name", "")
    event_group = first.get("underlying_event_group", "")

    paths: List[Path] = []
    if region == "US":
        paths.append(evidence_root / "shared" / "us_midterms_context.md")
    elif region == "Brazil":
        paths.append(evidence_root / "shared" / "brazil_context.md")
    elif region:
        paths.append(evidence_root / "shared" / "global_satellite_context.md")

    if bucket:
        paths.append(evidence_root / "shared" / f"{slugify(bucket)}.md")
    if system_type:
        paths.append(evidence_root / "shared" / f"{slugify(system_type)}.md")
    if event_group:
        paths.append(evidence_root / "parents" / f"{slugify(event_group)}.md")
    if parent:
        paths.append(evidence_root / "parents" / f"{slugify(parent)}.md")

    # Include row-specific notes for small groups only; large groups blow context.
    if len(group_rows) <= 8:
        for row in group_rows:
            tid = row.get("tracking_id", "")
            if tid:
                paths.append(evidence_root / "markets" / f"{tid}.md")

    out: List[Path] = []
    seen = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def load_evidence_for_group(
    group_rows: List[Dict[str, str]], evidence_root: Path, max_chars: int
) -> Tuple[str, List[str]]:
    chunks: List[str] = []
    used: List[str] = []
    for path in evidence_paths_for_group(group_rows, evidence_root):
        text = read_text_if_exists(path)
        if text:
            used.append(str(path))
            chunks.append(f"### {path}\n{text}")

    if not chunks:
        return "No evidence packet was found. Use broad base rates and keep confidence low.", []

    evidence_text = "\n\n".join(chunks)
    if len(evidence_text) > max_chars:
        evidence_text = evidence_text[:max_chars] + "\n\n[TRUNCATED]"
    return evidence_text, used


def build_options_text(group_rows: List[Dict[str, str]]) -> str:
    lines = []
    for i, row in enumerate(group_rows, start=1):
        lines.append(
            f"{i}. tracking_id={row.get('tracking_id', '')} | outcome={row.get('primary_outcome_to_track', '')} | question={row.get('outcome_contract_question', '')}"
        )
    return "\n".join(lines)


def build_prompt(group_rows: List[Dict[str, str]], evidence_text: str) -> str:
    first = group_rows[0]
    return USER_PROMPT_TEMPLATE.format(
        parent_market_name=first.get("parent_market_name", ""),
        region=first.get("region", ""),
        bucket=first.get("bucket", ""),
        system_type=first.get("system_type", ""),
        underlying_event_group=first.get("underlying_event_group", ""),
        options_text=build_options_text(group_rows),
        evidence_text=evidence_text,
    )


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def call_ollama(
    prompt: str, model: str, ollama_url: str, temperature: float, num_ctx: int, timeout: int
) -> Tuple[str, Optional[str]]:
    payload = {
        "model": model,
        "prompt": SYSTEM_INSTRUCTIONS + "\n\n" + prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    try:
        resp = requests.post(ollama_url.rstrip("/") + "/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "")), None
    except Exception as exc:
        return "", str(exc)


def mock_group_response(group_rows: List[Dict[str, str]]) -> Dict[str, Any]:
    # Deterministic simple distribution weighted by stable hashes.
    weights = []
    for row in group_rows:
        h = int(hashlib.sha256(row.get("tracking_id", "").encode()).hexdigest()[:8], 16)
        weights.append(1 + (h % 100))
    total = sum(weights)
    return {
        "group_confidence": 0.25,
        "group_rationale_short": "MOCK group estimate for pipeline testing only.",
        "key_uncertainties": ["mock mode", "no real evidence used"],
        "allocations": [
            {
                "tracking_id": row.get("tracking_id", ""),
                "fair_value": weights[i] / total,
                "rationale_short": "Mock allocation.",
            }
            for i, row in enumerate(group_rows)
        ],
    }


def parse_group_response(
    obj: Dict[str, Any],
    group_rows: List[Dict[str, str]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any], str]:
    tids = {r.get("tracking_id", "") for r in group_rows}
    allocations_raw = obj.get("allocations", [])
    if not isinstance(allocations_raw, list):
        allocations_raw = []

    parsed: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for alloc in allocations_raw:
        if not isinstance(alloc, dict):
            continue
        tid = str(alloc.get("tracking_id", "")).strip()
        if tid not in tids:
            continue
        fv = clamp_probability(alloc.get("fair_value"))
        if fv is None:
            continue
        parsed[tid] = {
            "fair_value": fv,
            "rationale_short": str(alloc.get("rationale_short", "")).strip()[:700],
        }

    missing = [tid for tid in tids if tid not in parsed]
    if missing:
        errors.append(f"missing_allocations={len(missing)}")
        # Assign zero to missing; normalization will handle, but error remains.
        for tid in missing:
            parsed[tid] = {"fair_value": 0.0, "rationale_short": ""}

    raw_sum = sum(v["fair_value"] for v in parsed.values())
    if raw_sum <= 0:
        errors.append("allocation_sum_zero")
        uniform = 1 / max(1, len(group_rows))
        for tid in tids:
            parsed[tid]["fair_value_norm"] = uniform
    else:
        for tid in tids:
            parsed[tid]["fair_value_norm"] = parsed[tid]["fair_value"] / raw_sum

    key_uncertainties = obj.get("key_uncertainties", [])
    if isinstance(key_uncertainties, str):
        key_uncertainties = [key_uncertainties]
    if not isinstance(key_uncertainties, list):
        key_uncertainties = []

    meta = {
        "group_confidence": clamp_probability(obj.get("group_confidence"))
        if obj.get("group_confidence") is not None
        else "",
        "group_rationale_short": str(obj.get("group_rationale_short", "")).strip()[:1000],
        "key_uncertainties": [str(x).strip() for x in key_uncertainties if str(x).strip()][:6],
        "allocation_sum_raw": round(raw_sum, 8),
        "normalization_applied": abs(raw_sum - 1.0) > 0.02,
    }

    status = "OK" if not errors else "INVALID:" + "|".join(errors)
    return parsed, meta, status


def group_rows(
    rows: List[Dict[str, str]], min_group_size: int, only_flagged: bool
) -> List[Tuple[str, List[Dict[str, str]]]]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        if row.get("signal_ready", "").lower() != "true":
            continue
        key = row.get("parent_market_name", "")
        groups.setdefault(key, []).append(row)

    out = []
    for key, items in groups.items():
        if len(items) < min_group_size:
            continue
        # Always group-prompt multi-option sets. Binary optional via min_group_size=2.
        out.append((key, items))
    return sorted(out, key=lambda kv: kv[0])


def estimate_group(
    key: str,
    rows: List[Dict[str, str]],
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
) -> List[Dict[str, str]]:
    evidence_text, evidence_paths = load_evidence_for_group(rows, evidence_root, max_chars=max_evidence_chars)
    prompt = build_prompt(rows, evidence_text)
    prompt_hash = stable_hash(prompt)

    raw_response = ""
    error = ""
    obj: Optional[Dict[str, Any]] = None

    if mock:
        obj = mock_group_response(rows)
        raw_response = json.dumps(obj, ensure_ascii=False)
    else:
        for attempt in range(1, retries + 1):
            raw_response, error = call_ollama(prompt, model, ollama_url, temperature, num_ctx, timeout)
            obj = extract_json_object(raw_response)
            if obj is not None:
                break
            time.sleep(attempt)

    if obj is None:
        parsed = {
            r.get("tracking_id", ""): {"fair_value": "", "fair_value_norm": "", "rationale_short": ""} for r in rows
        }
        meta = {
            "group_confidence": "",
            "group_rationale_short": "",
            "key_uncertainties": [],
            "allocation_sum_raw": "",
            "normalization_applied": "",
        }
        parse_status = "FAILED_PARSE"
    else:
        parsed, meta, parse_status = parse_group_response(obj, rows)

    output: List[Dict[str, str]] = []
    first = rows[0]
    for row in rows:
        tid = row.get("tracking_id", "")
        alloc = parsed.get(tid, {})
        out = {col: "" for col in OUTPUT_COLUMNS}
        out.update(
            {
                "group_estimate_id": f"{run_id}:{key}:{tid}",
                "run_id": run_id,
                "timestamp_utc": timestamp_utc,
                "prompt_version": PROMPT_VERSION,
                "model_backend": model_backend,
                "model_name": model,
                "parent_market_name": row.get("parent_market_name", ""),
                "group_key": key,
                "group_n": str(len(rows)),
                "tracking_id": tid,
                "primary_outcome_to_track": row.get("primary_outcome_to_track", ""),
                "outcome_contract_question": row.get("outcome_contract_question", ""),
                "region": row.get("region", ""),
                "bucket": row.get("bucket", ""),
                "system_type": row.get("system_type", ""),
                "underlying_event_group": row.get("underlying_event_group", ""),
                "gamma_market_id": row.get("gamma_market_id", ""),
                "condition_id": row.get("condition_id", ""),
                "exact_polymarket_slug": row.get("exact_polymarket_slug", ""),
                "market_url": row.get("market_url", ""),
                "token_id": row.get("token_id", ""),
                "group_fair_value_raw": str(alloc.get("fair_value", "")),
                "group_fair_value_norm": str(round(float(alloc["fair_value_norm"]), 8))
                if alloc.get("fair_value_norm") != ""
                else "",
                "group_confidence": str(meta.get("group_confidence", "")),
                "outcome_rationale_short": alloc.get("rationale_short", ""),
                "group_rationale_short": meta.get("group_rationale_short", ""),
                "key_uncertainties_json": json.dumps(meta.get("key_uncertainties", []), ensure_ascii=False),
                "allocation_sum_raw": str(meta.get("allocation_sum_raw", "")),
                "normalization_applied": str(meta.get("normalization_applied", "")).lower(),
                "evidence_packet_paths_json": json.dumps(evidence_paths, ensure_ascii=False),
                "prompt_hash": prompt_hash,
                "raw_response": raw_response,
                "parse_status": parse_status,
                "error": error if parse_status == "OK" else (error or parse_status),
                "comparison_price": row.get("comparison_price", ""),
                "price_type": row.get("price_type", ""),
                "liquidity_flags": row.get("liquidity_flags", ""),
                "signal_ready": row.get("signal_ready", ""),
            }
        )
        output.append(out)

    return output


def write_health(path: Path, latest_path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run group-level blind fair-value estimates.")
    p.add_argument("--input", default="data/signal_inputs/signal_input_latest.csv")
    p.add_argument("--latest-output", default="data/llm_group_estimates/llm_group_estimates_latest.csv")
    p.add_argument("--snapshot-dir", default="data/llm_group_estimates")
    p.add_argument("--append-output", default="data/snapshots/llm_group_estimate_snapshots.csv")
    p.add_argument("--health-dir", default="data/health")
    p.add_argument("--evidence-root", default="evidence")
    p.add_argument("--model", default=os.getenv("REALITY_SPREAD_LLM_MODEL", "qwen3:8b"))
    p.add_argument("--ollama-url", default=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL))
    p.add_argument("--model-backend", default="ollama", choices=["ollama", "mock"])
    p.add_argument("--mock", action="store_true")
    p.add_argument("--min-group-size", type=int, default=3, help="Default 3 means only multi-option groups.")
    p.add_argument("--limit-groups", type=int, default=None)
    p.add_argument("--temperature", type=float, default=0.12)
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--max-evidence-chars", type=int, default=14000)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    timestamp = utc_now()
    timestamp_utc = iso_utc(timestamp)
    run_id = timestamp.strftime("%Y%m%dT%H%M%SZ")

    rows = read_csv(Path(args.input))
    groups = group_rows(rows, min_group_size=args.min_group_size, only_flagged=False)
    if args.limit_groups is not None:
        groups = groups[: args.limit_groups]

    mock = args.mock or args.model_backend == "mock"
    model_backend = "mock" if mock else args.model_backend

    print(f"Run ID: {run_id}")
    print(f"Groups: {len(groups)}")
    print(f"Model: {args.model}")
    print(f"Backend: {model_backend}")

    all_outputs: List[Dict[str, str]] = []
    for i, (key, items) in enumerate(groups, start=1):
        print(f"[{i}/{len(groups)}] {key} ({len(items)} options)")
        outputs = estimate_group(
            key,
            items,
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
        all_outputs.extend(outputs)
        write_csv(Path(args.latest_output), all_outputs, OUTPUT_COLUMNS, append=False)

    snapshot_output = Path(args.snapshot_dir) / f"llm_group_estimates_{run_id}.csv"
    append_output = Path(args.append_output)

    write_csv(Path(args.latest_output), all_outputs, OUTPUT_COLUMNS, append=False)
    write_csv(snapshot_output, all_outputs, OUTPUT_COLUMNS, append=False)
    write_csv(append_output, all_outputs, OUTPUT_COLUMNS, append=True)

    parse_counts: Dict[str, int] = {}
    normalization_count = 0
    group_keys = set()
    for row in all_outputs:
        parse_counts[row["parse_status"]] = parse_counts.get(row["parse_status"], 0) + 1
        if row.get("normalization_applied") == "true":
            normalization_count += 1
        group_keys.add(row["group_key"])

    report = {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "input_path": args.input,
        "latest_output": args.latest_output,
        "snapshot_output": str(snapshot_output),
        "append_output": str(append_output),
        "prompt_version": PROMPT_VERSION,
        "model_backend": model_backend,
        "model_name": args.model,
        "mock": mock,
        "groups_total": len(groups),
        "rows_total": len(all_outputs),
        "parse_status_counts": parse_counts,
        "normalization_applied_rows": normalization_count,
        "group_keys": sorted(group_keys),
        "sample_errors": [
            {
                "group_key": r["group_key"],
                "tracking_id": r["tracking_id"],
                "parse_status": r["parse_status"],
                "error": r["error"],
                "raw_response": r["raw_response"][:500],
            }
            for r in all_outputs
            if r["parse_status"] != "OK"
        ][:20],
    }

    health_path = Path(args.health_dir) / f"llm_group_estimate_health_{run_id}.json"
    latest_health_path = Path(args.health_dir) / "latest_llm_group_estimate_health.json"
    write_health(health_path, latest_health_path, report)

    print("\nDone.")
    print(f"Groups: {report['groups_total']}")
    print(f"Rows: {report['rows_total']}")
    print(f"Parse status: {parse_counts}")
    print(f"Wrote latest: {args.latest_output}")
    print(f"Wrote health: {latest_health_path}")

    if report["rows_total"] == 0:
        raise SystemExit("No group estimate rows produced.")


if __name__ == "__main__":
    main()
