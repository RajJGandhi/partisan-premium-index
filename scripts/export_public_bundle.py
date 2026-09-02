from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import (
    BlindIndexRun,
    DailyIndex,
    EvidenceItem,
    FairValue,
    FairValueComponent,
    FairValueRevision,
    JobRun,
    Market,
    MarketResolution,
    MarketSnapshot,
    MarketSource,
    Prediction,
    SourceRun,
)
from app.ppi.job_run_lifecycle import run_status_summary
from app.ppi.public_forecast import PublicForecast, current_public_forecasts

SCHEMA_VERSION = "1.0"
DEFAULT_OUTPUT_DIR = Path("web/public/data")
PUBLIC_EVIDENCE_STATUSES = {"APPROVED", "AUTO_ACCEPTED"}
MAX_PUBLIC_EVIDENCE_PER_MARKET = 50
MAX_RECENT_RUNS = 20
MAX_RECENT_REVISIONS = 10
FORBIDDEN_PUBLIC_KEYS = {
    "classifier_raw_json",
    "config_json",
    "content_text",
    "database_url",
    "justification",
    "notes",
    "password_hash",
    "published_by",
    "query",
    "raw_json",
    "recorded_by",
    "review_notes",
    "sanitized_error",
    "session_secret",
}
FORBIDDEN_SECRET_MARKERS = ("postgresql://", "postgres://", "$2a$", "$2b$", "$2y$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 12) if math.isfinite(parsed) else None


def _probability(value: Any) -> float | None:
    parsed = _number(value)
    if parsed is None or not 0 <= parsed <= 1:
        return None
    return parsed


def _text(value: Any, *, max_length: int = 10_000) -> str | None:
    if value is None:
        return None
    cleaned = str(value).replace("\x00", "").strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _json_value(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return parsed


def _safe_public_url(raw: str | None) -> str | None:
    value = _text(raw, max_length=2_000)
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        return None

    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, ""))


def _slug(market: Market) -> str:
    candidate = _text(market.slug, max_length=500) or _text(market.tracking_id, max_length=64)
    if candidate:
        return candidate.strip("/")
    return f"market-{market.id}"


def _market_url(market: Market) -> str | None:
    slug = _text(market.slug, max_length=500)
    if market.platform == "polymarket" and slug:
        return f"https://polymarket.com/event/{slug.strip('/')}"
    return None


def _current_values(
    market: Market,
    snapshot: MarketSnapshot | None,
    fair_value: FairValue | None,
) -> tuple[float | None, float | None, float | None]:
    market_probability = _probability(snapshot.comparison_price if snapshot else None)
    published_fair_value = _probability(fair_value.published_fair_yes if fair_value else None)
    if published_fair_value is None and snapshot:
        published_fair_value = _probability(snapshot.fair_value)
    premium = None
    if market_probability is not None and published_fair_value is not None:
        premium = _number(market_probability - published_fair_value)
    return market_probability, published_fair_value, premium


def _snapshot_payload(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        "observed_at": _iso(snapshot.timestamp),
        "snapshot_date": _iso(snapshot.snapshot_date),
        "snapshot_kind": snapshot.snapshot_kind,
        "market_probability": _probability(snapshot.comparison_price),
        "price_type": _text(snapshot.price_type, max_length=50),
        "best_bid": _probability(snapshot.yes_best_bid),
        "best_ask": _probability(snapshot.yes_best_ask),
        "last_trade": _probability(snapshot.last_trade_price),
        "spread": _number(snapshot.spread),
        "volume": _number(snapshot.volume),
        "liquidity": _number(snapshot.liquidity),
        "ppi_fair_value": _probability(snapshot.fair_value),
        "partisan_premium": _number(snapshot.partisan_premium),
        "freshness_status": snapshot.freshness_status,
        "pipeline_status": snapshot.pipeline_status,
        "is_stale": bool(snapshot.is_stale),
    }


def _evidence_payload(item: EvidenceItem) -> dict[str, Any] | None:
    url = _safe_public_url(item.canonical_url or item.original_url)
    if not url:
        return None
    return {
        "title": _text(item.title, max_length=500),
        "url": url,
        "source_type": _text(item.source_type, max_length=50),
        "source_name": _text(item.source_name, max_length=250),
        "published_at": _iso(item.published_at),
        "discovered_at": _iso(item.discovered_at),
        "category": _text(item.category, max_length=50),
        "direction": _text(item.direction, max_length=20),
        "relevance_score": _probability(item.relevance_score),
        "source_quality": _probability(item.source_quality),
        "estimated_magnitude": _number(item.estimated_magnitude),
        "summary": _text(item.summary, max_length=1_500),
    }


def _component_payload(component: FairValueComponent) -> dict[str, Any]:
    return {
        "type": component.component_type,
        "probability": _probability(component.probability),
        "weight": _probability(component.weight),
        "source_label": _text(component.source_label, max_length=250),
        "source_url": _safe_public_url(component.source_url),
        "observed_at": _iso(component.observed_at),
        "updated_at": _iso(component.updated_at),
    }


def _source_payload(source: MarketSource) -> dict[str, Any]:
    return {
        "type": source.source_type,
        "name": _text(source.name, max_length=250),
        "url": _safe_public_url(source.url),
    }



def _public_component_map(raw: str | None) -> dict[str, Any]:
    parsed = _json_value(raw, {})
    if not isinstance(parsed, dict):
        return {}
    public: dict[str, Any] = {}
    for component_type, value in parsed.items():
        if not isinstance(value, dict):
            continue
        public[str(component_type)] = {
            "probability": _probability(value.get("probability")),
            "source_label": _text(value.get("source_label"), max_length=250),
            "source_url": _safe_public_url(value.get("source_url")),
            "observed_at": _text(value.get("observed_at"), max_length=100),
        }
    return public


def _public_weight_map(raw: str | None) -> dict[str, float | None]:
    parsed = _json_value(raw, {})
    if not isinstance(parsed, dict):
        return {}
    return {str(key): _probability(value) for key, value in parsed.items()}


def _revision_payload(
    revision: FairValueRevision,
    evidence_by_id: dict[int, EvidenceItem],
) -> dict[str, Any]:
    evidence_urls: list[str] = []
    raw_ids = _json_value(revision.evidence_ids_json, [])
    if isinstance(raw_ids, list):
        for raw_id in raw_ids:
            try:
                evidence_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            url = _safe_public_url(evidence.canonical_url or evidence.original_url)
            if url and url not in evidence_urls:
                evidence_urls.append(url)

    components = _public_component_map(revision.components_json)
    weights = _public_weight_map(revision.weights_json)
    effective_weights = _public_weight_map(revision.effective_weights_json)
    return {
        "revision_number": revision.revision_number,
        "fair_value": _probability(revision.fair_value),
        "previous_fair_value": _probability(revision.previous_fair_value),
        "thesis": _text(revision.thesis, max_length=5_000),
        "published_at": _iso(revision.published_at),
        "components": components,
        "weights": weights,
        "effective_weights": effective_weights,
        "evidence_urls": evidence_urls,
        "is_correction": revision.correction_of_revision_id is not None,
    }


def _prediction_payload(
    prediction: Prediction,
    market: Market,
    resolution: MarketResolution | None,
) -> dict[str, Any]:
    return {
        "market_slug": _slug(market),
        "question": _text(market.question, max_length=1_000),
        "region": _text(market.region, max_length=100),
        "category": _text(market.category, max_length=100),
        "status": prediction.status,
        "initial_publication_at": _iso(prediction.initial_publication_at),
        "initial_ppi_fair_value": _probability(prediction.initial_fair_value),
        "initial_market_probability": _probability(prediction.initial_market_probability),
        "initial_thesis": _text(prediction.initial_thesis, max_length=5_000),
        "resolved_outcome": _probability(prediction.final_outcome),
        "resolved_label": _text(resolution.resolved_label, max_length=100) if resolution else None,
        "resolved_at": _iso(prediction.resolved_at or (resolution.resolved_at if resolution else None)),
        "resolution_source_url": _safe_public_url(resolution.source_url) if resolution else None,
        "ppi_brier_score": _number(prediction.ppi_brier_score),
        "market_brier_score": _number(prediction.market_brier_score),
        "ppi_advantage": _number(prediction.performance_difference),
    }


def _job_payload(job: JobRun) -> dict[str, Any]:
    return {
        "run_key": job.run_key,
        "job_name": job.job_name,
        "trigger_type": job.trigger_type,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "status": job.status,
        "run_classification": job.run_classification,
        "superseded": job.superseded_by_id is not None,
        "markets_attempted": job.markets_attempted,
        "markets_succeeded": job.markets_succeeded,
        "evidence_discovered": job.evidence_discovered,
        "evidence_relevant": job.evidence_relevant,
        "proposals_created": job.proposals_created,
        "snapshots_written": job.snapshots_written,
        "error_count": job.error_count,
        # Lifecycle observability -- all safe slugs, never a secret. workflow_run_id/git_sha
        # identify the attempt; error_stage names the coarse stage that failed (null on success).
        "workflow_run_id": job.workflow_run_id,
        "git_sha": job.git_sha,
        "error_stage": job.error_stage,
    }


def _latest_canonical_job(session: Session) -> JobRun | None:
    """The most recent run that is safe to present as "the" authoritative latest update.

    Only a run classified "canonical" (scheduled primary/backup slot, live Qwen throughout, zero
    fallback/contamination) and not superseded by a later run qualifies. A noncanonical, mixed,
    contaminated, failed, or adhoc run must never be presented to the public as if it were a
    trustworthy canonical update, even if it is the most recent row in job_runs.
    """
    return session.scalar(
        select(JobRun)
        .where(JobRun.run_classification == "canonical", JobRun.superseded_by_id.is_(None))
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


def _daily_index_payload(row: DailyIndex) -> dict[str, Any]:
    return {
        "date": _iso(row.index_date),
        "tracked_market_count": row.tracked_market_count,
        "fresh_market_count": row.fresh_market_count,
        "average_signed_premium": _number(row.average_signed_premium),
        "average_absolute_premium": _number(row.average_absolute_premium),
        "liquidity_weighted_premium": _number(row.liquidity_weighted_premium),
        "share_above_fair_value": _probability(row.share_above_fair_value),
        "methodology_label": row.methodology_label,
        "generated_at": _iso(row.generated_at),
        "status": row.status,
    }


def _blind_index_run_payload(row: BlindIndexRun) -> dict[str, Any]:
    """One row per canonical run_key -- the primary blind-Qwen series' own aggregate history,
    structurally separate from the legacy DailyIndex/_daily_index_payload above."""
    return {
        "run_key": row.run_key,
        "effective_timestamp": _iso(row.effective_timestamp),
        "market_count": row.market_count,
        "average_signed_premium": _number(row.average_signed_premium),
        "median_signed_premium": _number(row.median_signed_premium),
        "average_absolute_premium": _number(row.average_absolute_premium),
        "model_name": _text(row.model_name, max_length=100),
        "generated_at": _iso(row.generated_at),
    }


def build_public_bundle(session: Session, *, generated_at: datetime | None = None) -> dict[str, Any]:
    generated_at = generated_at or utcnow()
    markets = list(
        session.scalars(
            select(Market)
            .where(Market.enabled.is_(True))
            .order_by(Market.region, Market.category, Market.question)
        )
    )
    market_ids = [market.id for market in markets]
    market_by_id = {market.id: market for market in markets}

    snapshots_by_market: dict[int, list[MarketSnapshot]] = defaultdict(list)
    fair_values_by_market: dict[int, FairValue] = {}
    components_by_market: dict[int, list[FairValueComponent]] = defaultdict(list)
    revisions_by_market: dict[int, list[FairValueRevision]] = defaultdict(list)
    evidence_by_market: dict[int, list[EvidenceItem]] = defaultdict(list)
    sources_by_market: dict[int, list[MarketSource]] = defaultdict(list)
    predictions_by_market: dict[int, list[Prediction]] = defaultdict(list)
    resolutions_by_market: dict[int, MarketResolution] = {}
    evidence_by_id: dict[int, EvidenceItem] = {}
    public_forecasts: dict[int, PublicForecast] = current_public_forecasts(session, market_ids)

    if market_ids:
        for snapshot in session.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.market_id.in_(market_ids))
            .order_by(MarketSnapshot.market_id, MarketSnapshot.timestamp)
        ):
            snapshots_by_market[snapshot.market_id].append(snapshot)

        fair_values_by_market = {
            item.market_id: item
            for item in session.scalars(select(FairValue).where(FairValue.market_id.in_(market_ids)))
        }

        for component in session.scalars(
            select(FairValueComponent)
            .where(FairValueComponent.market_id.in_(market_ids))
            .order_by(FairValueComponent.market_id, FairValueComponent.component_type)
        ):
            components_by_market[component.market_id].append(component)

        for revision in session.scalars(
            select(FairValueRevision)
            .where(FairValueRevision.market_id.in_(market_ids))
            .order_by(FairValueRevision.market_id, FairValueRevision.revision_number)
        ):
            revisions_by_market[revision.market_id].append(revision)

        for item in session.scalars(
            select(EvidenceItem)
            .where(
                EvidenceItem.market_id.in_(market_ids),
                EvidenceItem.relevant.is_(True),
                EvidenceItem.review_status.in_(PUBLIC_EVIDENCE_STATUSES),
            )
            .order_by(EvidenceItem.market_id, EvidenceItem.published_at.desc(), EvidenceItem.discovered_at.desc())
        ):
            evidence_by_id[item.id] = item
            if len(evidence_by_market[item.market_id]) < MAX_PUBLIC_EVIDENCE_PER_MARKET:
                evidence_by_market[item.market_id].append(item)

        for source in session.scalars(
            select(MarketSource)
            .where(MarketSource.market_id.in_(market_ids), MarketSource.enabled.is_(True))
            .order_by(MarketSource.market_id, MarketSource.source_type, MarketSource.name)
        ):
            sources_by_market[source.market_id].append(source)

        for prediction in session.scalars(
            select(Prediction)
            .where(Prediction.market_id.in_(market_ids))
            .order_by(Prediction.initial_publication_at)
        ):
            predictions_by_market[prediction.market_id].append(prediction)

        resolutions_by_market = {
            item.market_id: item
            for item in session.scalars(select(MarketResolution).where(MarketResolution.market_id.in_(market_ids)))
        }

    market_summaries: list[dict[str, Any]] = []
    market_details: dict[str, dict[str, Any]] = {}
    all_revision_summaries: list[dict[str, Any]] = []
    all_prediction_payloads: list[dict[str, Any]] = []

    for market in markets:
        slug = _slug(market)
        snapshots = snapshots_by_market[market.id]
        latest_snapshot = snapshots[-1] if snapshots else None
        fair_value = fair_values_by_market.get(market.id)
        live_market_probability, legacy_fair_value, legacy_premium = _current_values(
            market, latest_snapshot, fair_value
        )
        public_forecast = public_forecasts[market.id]
        revisions = revisions_by_market[market.id]
        evidence_payloads = [
            payload
            for item in evidence_by_market[market.id]
            if (payload := _evidence_payload(item)) is not None
        ]
        revision_payloads = [_revision_payload(revision, evidence_by_id) for revision in revisions]
        prediction_payloads = [
            _prediction_payload(prediction, market, resolutions_by_market.get(market.id))
            for prediction in predictions_by_market[market.id]
        ]
        all_prediction_payloads.extend(prediction_payloads)

        summary = {
            "slug": slug,
            "tracking_id": _text(market.tracking_id, max_length=64),
            "question": _text(market.question, max_length=1_000),
            "category": _text(market.category, max_length=100),
            "region": _text(market.region, max_length=100),
            "status": market.status,
            "active": bool(market.active),
            "closed": bool(market.closed),
            "end_date": _iso(market.end_date),
            "market_url": _market_url(market),
            # The primary blind-Qwen series: publishes automatically once a canonical forecast is
            # persisted (see app.ppi.public_forecast) -- no human approval step gates this. These
            # three fields always describe the SAME observation (the price the forecast was
            # actually compared against at join time, not a fresher live price), so
            # partisan_premium always equals market_probability - ppi_fair_value exactly as shown.
            # Null unless forecast_status == "OK"; see forecast_status for why.
            "market_probability": public_forecast.market_probability,
            "ppi_fair_value": public_forecast.fair_value,
            "partisan_premium": public_forecast.partisan_premium,
            "forecast_status": public_forecast.forecast_status,
            "forecast_generated_at": _iso(public_forecast.generated_at),
            "forecast_run_key": public_forecast.run_key,
            "forecast_model_name": _text(public_forecast.model_name, max_length=100),
            "forecast_confidence": _probability(public_forecast.confidence),
            "forecast_rationale": _text(public_forecast.rationale, max_length=700),
            # Current live Polymarket price, independent of any forecast -- always shown when
            # available, unlike market_probability above which is null except on an OK forecast.
            "live_market_probability": live_market_probability,
            "price_type": _text(latest_snapshot.price_type, max_length=50) if latest_snapshot else None,
            "best_bid": _probability(latest_snapshot.yes_best_bid) if latest_snapshot else None,
            "best_ask": _probability(latest_snapshot.yes_best_ask) if latest_snapshot else None,
            "spread": _number(latest_snapshot.spread) if latest_snapshot else None,
            "volume": _number(latest_snapshot.volume if latest_snapshot else market.volume),
            "liquidity": _number(latest_snapshot.liquidity if latest_snapshot else market.liquidity),
            "freshness_status": latest_snapshot.freshness_status if latest_snapshot else "NO_DATA",
            "pipeline_status": latest_snapshot.pipeline_status if latest_snapshot else "NO_DATA",
            "is_stale": bool(latest_snapshot.is_stale) if latest_snapshot else True,
            "last_observed_at": _iso(latest_snapshot.timestamp) if latest_snapshot else None,
            "last_fair_value_publication_at": _iso(fair_value.last_published_at) if fair_value else None,
            "revision_count": len(revisions),
            "public_evidence_count": len(evidence_payloads),
            # The legacy human-approved weighted-fair-value series (FairValue/FairValueRevision).
            # Retained for auditability -- see revisions/predictions below -- but no longer the
            # current headline PPI figure; that is the blind-Qwen series above.
            "legacy_weighted": {
                "ppi_fair_value": legacy_fair_value,
                "partisan_premium": legacy_premium,
            },
        }
        market_summaries.append(summary)

        configured_weights = {
            "polling": _probability(market.polling_weight),
            "forecast": _probability(market.forecast_weight),
            "comparable": _probability(market.comparable_weight),
            "expert": _probability(market.expert_weight),
            "news": _probability(market.news_weight),
        }
        resolution = resolutions_by_market.get(market.id)
        market_details[slug] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _iso(generated_at),
            "market": {
                **summary,
                "description": _text(market.description, max_length=10_000),
                "rules": _text(market.rules, max_length=10_000),
                "resolution_source": _text(market.resolution_source, max_length=2_000),
                "current_thesis": _text(market.current_thesis, max_length=5_000),
                "configured_weights": configured_weights,
            },
            "latest_snapshot": _snapshot_payload(latest_snapshot) if latest_snapshot else None,
            "history": [_snapshot_payload(snapshot) for snapshot in snapshots],
            "components": [_component_payload(component) for component in components_by_market[market.id]],
            "revisions": revision_payloads,
            "evidence": evidence_payloads,
            "sources": [_source_payload(source) for source in sources_by_market[market.id]],
            "predictions": prediction_payloads,
            "resolution": (
                {
                    "outcome": _probability(resolution.resolved_outcome),
                    "label": _text(resolution.resolved_label, max_length=100),
                    "resolved_at": _iso(resolution.resolved_at),
                    "source_url": _safe_public_url(resolution.source_url),
                }
                if resolution
                else None
            ),
        }

        for revision_payload in revision_payloads:
            all_revision_summaries.append(
                {
                    "market_slug": slug,
                    "question": summary["question"],
                    "region": summary["region"],
                    "revision_number": revision_payload["revision_number"],
                    "fair_value": revision_payload["fair_value"],
                    "previous_fair_value": revision_payload["previous_fair_value"],
                    "published_at": revision_payload["published_at"],
                    "thesis": revision_payload["thesis"],
                }
            )

    market_summaries.sort(
        key=lambda item: (
            item["region"] or "",
            item["category"] or "",
            item["question"] or "",
        )
    )

    # "Published" means a canonical OK blind-Qwen forecast exists -- publication is automatic,
    # not gated on human review (see app.ppi.public_forecast). ABSTAINED/ERROR/FLAGGED/NONE
    # markets are never counted here or in the aggregate index below.
    published_markets = [item for item in market_summaries if item["forecast_status"] == "OK"]
    signed_premiums = [float(item["partisan_premium"]) for item in published_markets]
    average_signed = sum(signed_premiums) / len(signed_premiums) if signed_premiums else None
    average_absolute = (
        sum(abs(value) for value in signed_premiums) / len(signed_premiums) if signed_premiums else None
    )
    share_above = (
        sum(1 for value in signed_premiums if value > 0) / len(signed_premiums) if signed_premiums else None
    )

    daily_index_rows = list(session.scalars(select(DailyIndex).order_by(DailyIndex.index_date)))
    daily_index_history = [_daily_index_payload(row) for row in daily_index_rows]

    # Only canonical, non-superseded runs' aggregates are presented as trustworthy history --
    # same principle as _latest_canonical_job below, applied to the full history rather than just
    # the latest row.
    blind_index_rows = list(
        session.scalars(
            select(BlindIndexRun)
            .join(JobRun, BlindIndexRun.job_run_id == JobRun.id)
            .where(JobRun.run_classification == "canonical", JobRun.superseded_by_id.is_(None))
            .order_by(BlindIndexRun.effective_timestamp)
        )
    )
    blind_index_history = [_blind_index_run_payload(row) for row in blind_index_rows]

    jobs = list(
        session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(MAX_RECENT_RUNS))
    )
    latest_job = jobs[0] if jobs else None
    latest_canonical_job = _latest_canonical_job(session)
    latest_source_runs: list[SourceRun] = []
    if latest_job:
        latest_source_runs = list(
            session.scalars(
                select(SourceRun)
                .where(SourceRun.job_run_id == latest_job.id)
                .order_by(SourceRun.status, SourceRun.source_name)
            )
        )

    source_statuses = [
        {
            "market_slug": _slug(market_by_id[source_run.market_id])
            if source_run.market_id in market_by_id
            else None,
            "source_name": _text(source_run.source_name, max_length=250),
            "status": source_run.status,
            "started_at": _iso(source_run.started_at),
            "finished_at": _iso(source_run.finished_at),
            "items_discovered": source_run.items_discovered,
            "items_inserted": source_run.items_inserted,
            "retry_count": source_run.retry_count,
        }
        for source_run in latest_source_runs
    ]

    resolved_predictions = [item for item in all_prediction_payloads if item["status"] == "RESOLVED"]
    ppi_scores = [float(item["ppi_brier_score"]) for item in resolved_predictions if item["ppi_brier_score"] is not None]
    market_scores = [
        float(item["market_brier_score"])
        for item in resolved_predictions
        if item["market_brier_score"] is not None
    ]
    comparable_resolved = [
        item
        for item in resolved_predictions
        if item["ppi_brier_score"] is not None
        and item["market_brier_score"] is not None
        and item["ppi_advantage"] is not None
    ]

    overview = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "project": {
            "name": "Partisan Premium Index",
            "abbreviation": "PPI",
            "formula": "Polymarket implied probability - PPI fair value",
            "cadence": "Twice daily, plus material-event updates",
        },
        "coverage": {
            "tracked_markets": len(market_summaries),
            "fresh_markets": sum(1 for item in market_summaries if not item["is_stale"]),
            "published_markets": len(published_markets),
            "resolved_predictions": len(resolved_predictions),
        },
        "current_index": {
            "average_signed_premium": _number(average_signed),
            "average_absolute_premium": _number(average_absolute),
            "share_above_fair_value": _probability(share_above),
            "methodology_label": "equal_weight_current_published_markets",
        },
        # Only a canonical, non-superseded run is presented as "the" latest update here -- a
        # noncanonical/contaminated/failed/adhoc run being the most recent row must never read as
        # a trustworthy canonical refresh. See system_status.latest_run_any for full transparency.
        "latest_run": _job_payload(latest_canonical_job) if latest_canonical_job else None,
        "largest_positive_premiums": sorted(
            published_markets,
            key=lambda item: float(item["partisan_premium"]),
            reverse=True,
        )[:5],
        "largest_negative_premiums": sorted(
            published_markets,
            key=lambda item: float(item["partisan_premium"]),
        )[:5],
        "largest_absolute_premiums": sorted(
            published_markets,
            key=lambda item: abs(float(item["partisan_premium"])),
            reverse=True,
        )[:5],
        "recent_fair_value_revisions": sorted(
            all_revision_summaries,
            key=lambda item: item["published_at"] or "",
            reverse=True,
        )[:MAX_RECENT_REVISIONS],
        # Legacy human-weighted series' daily aggregate -- retained for auditability.
        "index_history": daily_index_history,
        # Primary blind-Qwen series' aggregate history, one entry per canonical run.
        "blind_index_history": blind_index_history,
    }

    track_record = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "summary": {
            "total_predictions": len(all_prediction_payloads),
            "open_predictions": sum(1 for item in all_prediction_payloads if item["status"] == "OPEN"),
            "resolved_predictions": len(resolved_predictions),
            "average_ppi_brier_score": _number(sum(ppi_scores) / len(ppi_scores)) if ppi_scores else None,
            "average_market_brier_score": (
                _number(sum(market_scores) / len(market_scores)) if market_scores else None
            ),
            "ppi_wins": sum(1 for item in comparable_resolved if float(item["ppi_advantage"]) > 0),
            "market_wins": sum(1 for item in comparable_resolved if float(item["ppi_advantage"]) < 0),
            "ties": sum(1 for item in comparable_resolved if float(item["ppi_advantage"]) == 0),
            "sample_warning": (
                "Sample size is too small for strong conclusions."
                if len(resolved_predictions) < 30
                else None
            ),
        },
        "predictions": all_prediction_payloads,
    }

    system_status = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(generated_at),
        "status": latest_job.status if latest_job else "NO_RUNS",
        # Unchanged field: most recent run of any classification, for full operational
        # transparency -- a noncanonical/contaminated/failed run must be visible here, just never
        # presented as "the" canonical update (that's latest_canonical_run below, and
        # overview.latest_run, which is canonical-only).
        "latest_run": _job_payload(latest_job) if latest_job else None,
        "latest_canonical_run": _job_payload(latest_canonical_job) if latest_canonical_job else None,
        "recent_runs": [_job_payload(job) for job in jobs],
        "latest_source_runs": source_statuses,
        # Compact, DB-derived end-to-end lifecycle health: last attempt, last (canonical) success,
        # last status, markets completed, error stage. Every value comes from job_runs -- never
        # hardcoded. Lets the frontend show "Last successful canonical run: <date>" and lets an
        # external check alert when no successful run exists for too long.
        "run_health": run_status_summary(session, now=generated_at),
        "summary": {
            "tracked_markets": len(market_summaries),
            "fresh_markets": sum(1 for item in market_summaries if not item["is_stale"]),
            "stale_markets": sum(1 for item in market_summaries if item["is_stale"]),
            "latest_source_failures": sum(1 for item in source_statuses if item["status"] not in {"OK", "SUCCESS"}),
        },
    }

    return {
        "overview": overview,
        "markets": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _iso(generated_at),
            "markets": market_summaries,
        },
        "track_record": track_record,
        "system_status": system_status,
        "market_details": market_details,
    }



def assert_public_bundle_safe(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Forbidden public field at {path}.{key}")
            assert_public_bundle_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            assert_public_bundle_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_SECRET_MARKERS):
            raise ValueError(f"Potential secret marker found at {path}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_public_bundle(bundle: dict[str, Any], output_dir: Path) -> list[Path]:
    assert_public_bundle_safe(bundle)
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    backup_dir = output_dir.with_name(f".{output_dir.name}.previous")
    written: list[Path] = []

    try:
        top_level_files = {
            "overview.json": bundle["overview"],
            "markets.json": bundle["markets"],
            "track-record.json": bundle["track_record"],
            "system-status.json": bundle["system_status"],
        }
        for name, payload in top_level_files.items():
            path = temp_dir / name
            _write_json(path, payload)
            written.append(output_dir / name)

        details: dict[str, Any] = bundle["market_details"]
        for slug, payload in sorted(details.items()):
            path = temp_dir / "markets" / f"{slug}.json"
            _write_json(path, payload)
            written.append(output_dir / "markets" / f"{slug}.json")

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": bundle["overview"]["generated_at"],
            "files": [str(path.relative_to(output_dir)) for path in written],
            "market_count": len(details),
        }
        _write_json(temp_dir / "manifest.json", manifest)
        written.append(output_dir / "manifest.json")

        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if output_dir.exists():
            os.replace(output_dir, backup_dir)
        os.replace(temp_dir, output_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        return written
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not output_dir.exists() and backup_dir.exists():
            os.replace(backup_dir, output_dir)
        raise


def export_public_bundle(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    with SessionLocal() as session:
        bundle = build_public_bundle(session)
    return write_public_bundle(bundle, output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export sanitized public PPI JSON for the static frontend.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    written = export_public_bundle(args.output_dir)
    print(
        json.dumps(
            {
                "status": "OK",
                "output_dir": str(args.output_dir),
                "files_written": len(written),
                "market_files": sum(1 for path in written if path.parent.name == "markets"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
