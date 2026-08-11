"""Read-only forensic audit of one JobRun's primary blind-LLM (Qwen) forecasts.

Strictly SELECT-only: `audit_job_run` never calls `session.add`/`session.delete`/`session.merge`,
never flushes or commits a change, never runs a migration, never generates a new forecast, and
never modifies `JobRun.superseded_by_id` or any other row. It exists specifically so an already-
completed canonical run can be inspected in detail (raw model responses, exact evidence packets,
retries, generation parameters) without needing local `DATABASE_URL` access -- see
`docs/OPERATIONS.md` "Read-only forecast run audit" and `.github/workflows/ppi-audit.yml`.

Never touches `DATABASE_URL` or any other secret in its output. `_assert_report_is_safe` scans
the full report for the same forbidden-secret markers `scripts/export_public_bundle.py` uses
before writing anything to disk, as defense in depth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_session
from app.db.models import EvidenceItem, JobRun, LLMForecast, Market
from scripts.export_public_bundle import FORBIDDEN_SECRET_MARKERS

FLOAT_EPSILON = 1e-9
TARGET_PROBABILITY = 0.45


def _sha256(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_evidence_ids(evidence_ids_json: str | None) -> list[int]:
    if not evidence_ids_json:
        return []
    try:
        parsed = json.loads(evidence_ids_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    ids: list[int] = []
    for item in parsed:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _load_generation_params(generation_params_json: str | None) -> dict[str, Any] | None:
    if not generation_params_json:
        return None
    try:
        return json.loads(generation_params_json)
    except json.JSONDecodeError:
        return {"_unparsed": generation_params_json}


@dataclass
class ForecastAuditRow:
    forecast_id: int
    market_id: int
    market_slug: str | None
    market_question: str | None
    # Reconstructing the exact blind evidence packet (e.g. for a replay/shadow experiment against
    # a frozen historical run) needs these, matching build_blind_evidence_packet's own packet
    # shape in app/ppi/blind_forecast.py.
    market_category: str | None
    market_region: str | None
    market_end_date: str | None
    market_resolution_criteria: str | None
    run_slot: str
    status: str
    fair_value: float | None
    confidence: float | None
    should_abstain: bool | None
    retries: int
    model_provider: str
    model_name: str
    prompt_version: str
    prompt_hash: str | None
    generation_params: dict[str, Any] | None
    raw_response: str | None
    raw_response_hash: str | None
    raw_response_contains_target_probability: bool
    thinking_mode_detected: bool
    evidence_ids: list[int]
    evidence_count: int
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    evidence_packet_hash: str | None = None
    evidence_all_live_classified: bool | None = None
    error_message: str | None = None
    reviewed_status: str = "UNREVIEWED"
    generated_at: str | None = None


def _audit_one_forecast(session: Session, forecast: LLMForecast, market: Market | None) -> ForecastAuditRow:
    evidence_ids = _load_evidence_ids(forecast.evidence_ids_json)
    evidence_by_id: dict[int, EvidenceItem] = {}
    if evidence_ids:
        for item in session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))):
            evidence_by_id[item.id] = item
    # Preserve the order actually recorded in evidence_ids_json (the order shown to the model),
    # not whatever order the SELECT happens to return.
    ordered_evidence = [evidence_by_id[i] for i in evidence_ids if i in evidence_by_id]

    evidence_payload = [
        {
            "id": item.id,
            "title": item.title,
            # Matches build_blind_evidence_packet's own packet["evidence"][i]["summary"] exactly
            # (item.summary if set, else content_text, truncated to the same 600 chars) -- the
            # actual text shown to the model, needed to replay this evidence packet faithfully.
            "summary": (item.summary or item.content_text or "")[:600],
            "source_name": item.source_name,
            "source_type": item.source_type,
            "url": item.canonical_url or item.original_url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "category": item.category,
            "content_hash": item.content_hash,
            "classifier_provider": item.classifier_provider,
        }
        for item in ordered_evidence
    ]
    packet_key = json.dumps([e["content_hash"] for e in evidence_payload])
    raw = forecast.raw_response

    return ForecastAuditRow(
        forecast_id=forecast.id,
        market_id=forecast.market_id,
        market_slug=market.slug if market else None,
        market_question=market.question if market else None,
        market_category=market.category if market else None,
        market_region=market.region if market else None,
        market_end_date=market.end_date.isoformat() if market and market.end_date else None,
        market_resolution_criteria=(market.rules or market.resolution_source or "") if market else None,
        run_slot=forecast.run_slot,
        status=forecast.status,
        fair_value=forecast.fair_value,
        confidence=forecast.confidence,
        should_abstain=forecast.should_abstain,
        retries=forecast.retries,
        model_provider=forecast.model_provider,
        model_name=forecast.model_name,
        prompt_version=forecast.prompt_version,
        prompt_hash=forecast.prompt_hash,
        generation_params=_load_generation_params(forecast.generation_params_json),
        raw_response=raw,
        raw_response_hash=_sha256(raw),
        raw_response_contains_target_probability=bool(raw and str(TARGET_PROBABILITY) in raw),
        thinking_mode_detected=bool(raw and ("<think>" in raw or "</think>" in raw)),
        evidence_ids=evidence_ids,
        evidence_count=len(evidence_payload),
        evidence_items=evidence_payload,
        evidence_packet_hash=_sha256(packet_key) if evidence_payload else None,
        evidence_all_live_classified=forecast.evidence_all_live_classified,
        error_message=forecast.error_message,
        reviewed_status=forecast.reviewed_status,
        generated_at=forecast.generated_at.isoformat() if forecast.generated_at else None,
    )


def _build_aggregate(rows: list[ForecastAuditRow]) -> dict[str, Any]:
    unique_raw_hashes = {r.raw_response_hash for r in rows if r.raw_response_hash}
    unique_probabilities = sorted({r.fair_value for r in rows if r.fair_value is not None})
    count_at_target = sum(
        1 for r in rows if r.fair_value is not None and abs(r.fair_value - TARGET_PROBABILITY) < FLOAT_EPSILON
    )
    target_in_raw = [r.market_slug or r.market_id for r in rows if r.raw_response_contains_target_probability]

    hashes_by_market: dict[int, set[str]] = {
        r.market_id: {e["content_hash"] for e in r.evidence_items if e.get("content_hash")} for r in rows
    }
    pair_overlaps = []
    for (market_a, hashes_a), (market_b, hashes_b) in combinations(hashes_by_market.items(), 2):
        shared = hashes_a & hashes_b
        if shared:
            pair_overlaps.append(
                {
                    "market_id_a": market_a,
                    "market_id_b": market_b,
                    "shared_content_hashes": sorted(shared),
                    "overlap_count": len(shared),
                }
            )

    hash_to_markets: dict[str, set[int]] = defaultdict(set)
    hash_to_title: dict[str, str] = {}
    for row in rows:
        for item in row.evidence_items:
            content_hash = item.get("content_hash")
            if content_hash:
                hash_to_markets[content_hash].add(row.market_id)
                hash_to_title.setdefault(content_hash, item.get("title") or "")
    repeated_sources = [
        {
            "content_hash": content_hash,
            "title": hash_to_title[content_hash],
            "market_count": len(markets),
            "market_ids": sorted(markets),
        }
        for content_hash, markets in hash_to_markets.items()
        if len(markets) > 1
    ]

    packet_hash_counts = Counter(r.evidence_packet_hash for r in rows if r.evidence_packet_hash)
    identical_packets = sorted(h for h, count in packet_hash_counts.items() if count > 1)

    return {
        "forecast_count": len(rows),
        "unique_raw_response_count": len(unique_raw_hashes),
        "unique_probability_count": len(unique_probabilities),
        "unique_probabilities": unique_probabilities,
        "count_at_target_probability": count_at_target,
        "target_probability": TARGET_PROBABILITY,
        "markets_with_target_probability_in_raw_response": target_in_raw,
        "evidence_pair_overlaps": pair_overlaps,
        "repeated_evidence_sources": repeated_sources,
        "identical_evidence_packets": identical_packets,
        "thinking_mode_detected_count": sum(1 for r in rows if r.thinking_mode_detected),
    }


def audit_job_run(session: Session, job_run_id: int) -> dict[str, Any]:
    """Pure read-only query: builds the full forensic report for one JobRun.

    Never calls session.add/delete/merge/flush/commit -- only select() via session.scalars/get.
    Raises ValueError if no such JobRun exists; never creates one.
    """
    job = session.get(JobRun, job_run_id)
    if job is None:
        raise ValueError(f"No JobRun with id={job_run_id}")

    forecasts = list(
        session.scalars(
            select(LLMForecast).where(LLMForecast.job_run_id == job_run_id).order_by(LLMForecast.market_id)
        )
    )
    market_ids = {f.market_id for f in forecasts}
    markets_by_id: dict[int, Market] = {}
    if market_ids:
        for market in session.scalars(select(Market).where(Market.id.in_(market_ids))):
            markets_by_id[market.id] = market

    rows = [_audit_one_forecast(session, forecast, markets_by_id.get(forecast.market_id)) for forecast in forecasts]

    return {
        "job_run_id": job.id,
        "run_key": job.run_key,
        "run_classification": job.run_classification,
        "pipeline_mode": job.pipeline_mode,
        "trigger_type": job.trigger_type,
        "status": job.status,
        "superseded_by_id": job.superseded_by_id,
        "forecasts": [asdict(row) for row in rows],
        "aggregate": _build_aggregate(rows),
    }


def _assert_report_is_safe(value: Any, *, path: str = "root") -> None:
    """Mirrors scripts.export_public_bundle.assert_public_bundle_safe's secret-marker scan.

    Nothing in this report is a secret by construction (only forecast/evidence content and
    metadata columns, never DATABASE_URL or connection details) -- this is defense in depth, not
    the primary safeguard.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_report_is_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_report_is_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_SECRET_MARKERS):
            raise ValueError(f"Potential secret marker found at {path}; refusing to write audit report.")


def render_markdown_summary(report: dict[str, Any]) -> str:
    """Aggregate-only Markdown summary, safe for $GITHUB_STEP_SUMMARY.

    Deliberately excludes raw_response text and evidence titles/content -- only counts, hashes,
    and identifiers. The full per-forecast detail belongs in the uploaded JSON artifact, not here.
    """
    agg = report["aggregate"]
    lines = [
        "## LLM forecast run audit (read-only)",
        "",
        f"- Job run ID: `{report['job_run_id']}`",
        f"- Run key: `{report['run_key']}`",
        f"- Run classification: `{report['run_classification']}` (superseded_by_id: `{report['superseded_by_id']}`)",
        f"- Forecast count: {agg['forecast_count']}",
        f"- Unique raw responses: {agg['unique_raw_response_count']}",
        f"- Unique probabilities: {agg['unique_probability_count']} ({agg['unique_probabilities']})",
        f"- Count at exactly {agg['target_probability']}: {agg['count_at_target_probability']}",
        f"- Markets with `{agg['target_probability']}` literally in raw_response: "
        f"{len(agg['markets_with_target_probability_in_raw_response'])}",
        f"- Evidence-pair overlaps found: {len(agg['evidence_pair_overlaps'])}",
        f"- Repeated evidence sources across markets: {len(agg['repeated_evidence_sources'])}",
        f"- Identical evidence packets: {len(agg['identical_evidence_packets'])}",
        f"- Thinking-mode detected in raw_response: {agg['thinking_mode_detected_count']}/{agg['forecast_count']}",
        "",
        "| Market | Status | Fair value | Confidence | Retries | Evidence count |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report["forecasts"]:
        lines.append(
            f"| {row['market_slug'] or row['market_id']} | {row['status']} | {row['fair_value']} | "
            f"{row['confidence']} | {row['retries']} | {row['evidence_count']} |"
        )
    lines.append("")
    lines.append("Full per-forecast detail (raw responses, evidence text) is in the uploaded artifact, not here.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only forensic audit of one JobRun's LLM forecasts")
    parser.add_argument("--job-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, default=None, help="Write the full JSON report here (default: stdout)")
    parser.add_argument(
        "--markdown-summary",
        type=Path,
        default=None,
        help="Write an aggregate-only Markdown summary here (safe for $GITHUB_STEP_SUMMARY)",
    )
    args = parser.parse_args()

    with get_session() as session:
        report = audit_job_run(session, args.job_run_id)

    _assert_report_is_safe(report)

    payload = json.dumps(report, indent=2, default=str)
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload)

    if args.markdown_summary:
        args.markdown_summary.write_text(render_markdown_summary(report))


if __name__ == "__main__":
    main()
