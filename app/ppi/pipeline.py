from __future__ import annotations

import json
import traceback
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import requests
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_session, init_db
from app.db.models import (
    DailyIndex,
    EvidenceItem,
    FairValue,
    FairValueComponent,
    FairValueProposal,
    JobRun,
    Market,
    MarketSnapshot,
    MarketSource,
    SourceRun,
)
from app.ppi.digest import write_daily_digest
from app.ppi.evidence import adapter_for, insert_and_classify_candidate
from app.ppi.methodology import DEFAULT_WEIGHTS, compute_weighted_fair_value, partisan_premium
from app.ppi.polymarket import TrackedPolymarketClient, price_policy, save_raw_response, update_market_from_gamma


def utcnow() -> datetime:
    return datetime.now(UTC)


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:700]}"


def _upstream_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, tz=UTC)
    except Exception:
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except Exception:
            return None


def _weights(market: Market) -> dict[str, float]:
    return {
        "polling": market.polling_weight,
        "forecast": market.forecast_weight,
        "comparable": market.comparable_weight,
        "expert": market.expert_weight,
        "news": market.news_weight,
    }


def _components(
    session: Session, market: Market
) -> tuple[dict[str, float | None], dict[str, bool], dict[str, dict[str, Any]]]:
    rows = list(session.scalars(select(FairValueComponent).where(FairValueComponent.market_id == market.id)))
    by_type = {r.component_type: r for r in rows}
    values: dict[str, float | None] = {}
    eligible: dict[str, bool] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for component in DEFAULT_WEIGHTS:
        row = by_type.get(component)
        values[component] = row.probability if row else None
        eligible[component] = row.eligible_for_redistribution if row else True
        provenance[component] = {
            "probability": row.probability if row else None,
            "source_label": row.source_label if row else None,
            "source_url": row.source_url if row else None,
            "observed_at": row.observed_at.isoformat() if row and row.observed_at else None,
            "ingestion_method": row.ingestion_method if row else None,
            "notes": row.notes if row else None,
        }
    return values, eligible, provenance


def _upsert_daily_snapshot(
    session: Session,
    market: Market,
    book: dict[str, Any] | None,
    evidence: list[EvidenceItem],
    proposals: list[FairValueProposal],
    status: str,
    message: str,
    stale: bool,
) -> MarketSnapshot:
    today = utcnow().date()
    snap = session.scalar(
        select(MarketSnapshot).where(
            MarketSnapshot.market_id == market.id,
            MarketSnapshot.snapshot_date == today,
            MarketSnapshot.snapshot_kind == "daily",
        )
    )
    if not snap:
        snap = MarketSnapshot(market_id=market.id, snapshot_date=today, snapshot_kind="daily")
        session.add(snap)
    book = book or {}
    policy = price_policy(book)
    fv = session.scalar(select(FairValue).where(FairValue.market_id == market.id))
    published = fv.published_fair_yes if fv else None
    values, eligible, provenance = _components(session, market)
    methodology = compute_weighted_fair_value(values, _weights(market), eligible)
    snap.timestamp = utcnow()
    snap.yes_price_displayed = policy["comparison_price"]
    snap.no_price_displayed = 1 - policy["comparison_price"] if policy["comparison_price"] is not None else None
    snap.yes_best_bid = book.get("yes_best_bid")
    snap.yes_best_ask = book.get("yes_best_ask")
    snap.no_best_bid = book.get("no_best_bid")
    snap.no_best_ask = book.get("no_best_ask")
    snap.yes_midpoint = book.get("yes_midpoint")
    snap.no_midpoint = book.get("no_midpoint")
    snap.last_trade_price = book.get("last_trade_price")
    snap.comparison_price = policy["comparison_price"]
    snap.price_type = policy["price_type"]
    snap.executable_buy_price = policy["executable_buy_price"]
    snap.executable_sell_price = policy["executable_sell_price"]
    snap.spread = book.get("spread")
    snap.depth_1c = book.get("depth_1c")
    snap.depth_3c = book.get("depth_3c")
    snap.depth_5c = book.get("depth_5c")
    snap.volume = market.volume
    snap.liquidity = market.liquidity
    snap.fair_value = published
    snap.partisan_premium = partisan_premium(policy["comparison_price"], published)
    snap.component_inputs_json = json.dumps(provenance, default=str)
    snap.component_weights_json = json.dumps(methodology.original_weights)
    snap.effective_weights_json = json.dumps(methodology.effective_weights)
    snap.evidence_ids_json = json.dumps([e.id for e in evidence])
    snap.pending_proposal_ids_json = json.dumps([p.id for p in proposals])
    snap.freshness_status = "STALE" if stale else "FRESH"
    snap.pipeline_status = status
    snap.status_message = message
    snap.is_stale = stale
    snap.raw_orderbook_json = book.get("raw_orderbook_json")
    session.flush()
    return snap


def _maybe_create_proposal(session: Session, market: Market, evidence: list[EvidenceItem]) -> FairValueProposal | None:
    values, eligible, provenance = _components(session, market)
    result = compute_weighted_fair_value(values, _weights(market), eligible)
    if result.fair_value is None:
        return None
    fv = session.scalar(select(FairValue).where(FairValue.market_id == market.id))
    current = fv.published_fair_yes if fv else None
    material_evidence = [e for e in evidence if e.relevant and e.changes_probability]
    changed = current is None or abs(result.fair_value - current) >= 0.005
    if not changed and not material_evidence:
        return None
    today = utcnow().date()
    day_start = datetime.combine(today, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    existing = session.scalar(
        select(FairValueProposal).where(
            FairValueProposal.market_id == market.id,
            FairValueProposal.status == "PENDING",
            FairValueProposal.created_at >= day_start,
            FairValueProposal.created_at < day_end,
        )
    )
    if existing:
        return existing
    rationale_parts = []
    if changed:
        rationale_parts.append("Component inputs produce a different proposed fair value.")
    if material_evidence:
        rationale_parts.append(f"{len(material_evidence)} newly classified evidence item(s) may change probability.")
    if result.warnings:
        rationale_parts.extend(result.warnings)
    proposal = FairValueProposal(
        market_id=market.id,
        proposed_fair_value=result.fair_value,
        current_published_fair_value=current,
        proposed_components_json=json.dumps(provenance, default=str),
        proposed_weights_json=json.dumps(result.original_weights),
        effective_weights_json=json.dumps(result.effective_weights),
        evidence_ids_json=json.dumps([e.id for e in material_evidence]),
        rationale=" ".join(rationale_parts) or "Proposed from current component inputs.",
        confidence=max(0.0, 1.0 - 0.12 * len(result.missing_components)),
        proposed_by="daily_pipeline",
    )
    session.add(proposal)
    session.flush()
    return proposal


def _collect_market_evidence(session: Session, job: JobRun, market: Market) -> tuple[list[EvidenceItem], int]:
    relevant_new: list[EvidenceItem] = []
    inserted_count = 0
    sources = list(
        session.scalars(select(MarketSource).where(MarketSource.market_id == market.id, MarketSource.enabled.is_(True)))
    )
    for source in sources:
        sr = SourceRun(job_run_id=job.id, market_id=market.id, market_source_id=source.id, source_name=source.name)
        session.add(sr)
        session.flush()
        try:
            adapter = adapter_for(source.source_type)
            candidates = adapter.collect(market, source) if adapter else []
            sr.items_discovered = len(candidates)
            for candidate in candidates:
                item, inserted = insert_and_classify_candidate(session, market, source, candidate)
                if inserted:
                    inserted_count += 1
                    sr.items_inserted += 1
                    if item.relevant:
                        relevant_new.append(item)
            sr.status = "OK"
        except Exception as exc:
            sr.status = "FAILED"
            sr.sanitized_error = _safe_error(exc)
            job.error_count += 1
        finally:
            sr.finished_at = utcnow()
    market.last_evidence_sync_at = utcnow()
    return relevant_new, inserted_count


def _update_daily_index(session: Session, day: date) -> DailyIndex:
    snapshots = list(
        session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.snapshot_date == day, MarketSnapshot.snapshot_kind == "daily")
        )
    )
    premiums = [s.partisan_premium for s in snapshots if s.partisan_premium is not None and not s.is_stale]
    fresh = [s for s in snapshots if not s.is_stale and s.pipeline_status in {"OK", "PARTIAL"}]
    liquidity_rows = [
        (s.partisan_premium, s.liquidity or 0)
        for s in fresh
        if s.partisan_premium is not None and (s.liquidity or 0) > 0
    ]
    row = session.scalar(select(DailyIndex).where(DailyIndex.index_date == day))
    if not row:
        row = DailyIndex(index_date=day)
        session.add(row)
    row.tracked_market_count = len(snapshots)
    row.fresh_market_count = len(fresh)
    row.average_signed_premium = sum(premiums) / len(premiums) if premiums else None
    row.average_absolute_premium = sum(abs(x) for x in premiums) / len(premiums) if premiums else None
    total_liquidity = sum(liq for _, liq in liquidity_rows)
    row.liquidity_weighted_premium = (
        sum(p * liq for p, liq in liquidity_rows) / total_liquidity if total_liquidity else None
    )
    row.share_above_fair_value = sum(1 for p in premiums if p > 0) / len(premiums) if premiums else None
    row.generated_at = utcnow()
    row.status = "OK" if len(fresh) == len(snapshots) and snapshots else "PARTIAL"
    session.flush()
    return row


def _send_discord_digest(job: JobRun, digest_markdown: str | None = None) -> None:
    webhook = get_settings().discord_webhook_url
    if not webhook:
        return
    header = (
        f"PPI daily run {job.status}: {job.markets_succeeded}/{job.markets_attempted} markets, "
        f"{job.evidence_relevant} relevant evidence items, {job.proposals_created} proposals, "
        f"{job.snapshots_written} snapshots, {job.error_count} errors.\n"
        f"Approval queue: {get_settings().app_base_url.rstrip('/')}/?page=Administration"
    )
    message = header
    if digest_markdown:
        # Discord messages are limited to 2,000 characters. Keep the durable full digest on disk.
        compact = "\n".join(
            line
            for line in digest_markdown.splitlines()
            if line.startswith("## ") or line.startswith("No ") or line.startswith("- [")
        )
        if compact:
            message = f"{header}\n\n{compact}"[:1950]
    try:
        requests.post(webhook, json={"content": message}, timeout=10).raise_for_status()
    except Exception:
        pass


def run_daily_pipeline(
    trigger_type: str = "manual", force: bool = False, run_date: date | None = None
) -> dict[str, Any]:
    init_db()
    day = run_date or utcnow().date()
    run_key = f"ppi-daily:{day.isoformat()}:{trigger_type}"
    with get_session() as session:
        existing = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        if existing and existing.status == "OK" and not force:
            return {"status": "ALREADY_COMPLETE", "run_key": run_key, "job_run_id": existing.id}
        if existing:
            job = existing
            session.execute(delete(SourceRun).where(SourceRun.job_run_id == job.id))
            job.status = "RUNNING"
            job.started_at = utcnow()
            job.finished_at = None
            job.sanitized_error = None
            job.markets_attempted = 0
            job.markets_succeeded = 0
            job.evidence_discovered = 0
            job.evidence_relevant = 0
            job.proposals_created = 0
            job.snapshots_written = 0
            job.error_count = 0
            job.metadata_json = None
        else:
            job = JobRun(run_key=run_key, job_name="daily_pipeline", trigger_type=trigger_type)
            session.add(job)
            session.flush()
        session.commit()
        client = TrackedPolymarketClient()
        markets = list(
            session.scalars(
                select(Market)
                .where(Market.enabled.is_(True), Market.active.is_(True), Market.closed.is_(False))
                .order_by(Market.id)
            )
        )
        job.markets_attempted = len(markets)
        try:
            for market in markets:
                market_errors: list[str] = []
                book: dict[str, Any] | None = None
                stale = False
                # Market metadata sync.
                sr = SourceRun(job_run_id=job.id, market_id=market.id, source_name="polymarket_gamma")
                session.add(sr)
                session.flush()
                try:
                    data, endpoint, code = client.fetch_market(market)
                    save_raw_response(session, market, "polymarket_gamma", endpoint, data, code)
                    update_market_from_gamma(market, data)
                    sr.status = "OK"
                    sr.items_discovered = 1
                    sr.items_inserted = 1
                except Exception as exc:
                    market_errors.append(_safe_error(exc))
                    sr.status = "FAILED"
                    sr.sanitized_error = _safe_error(exc)
                    job.error_count += 1
                finally:
                    sr.finished_at = utcnow()

                # Executable order-book pricing.
                sr = SourceRun(job_run_id=job.id, market_id=market.id, source_name="polymarket_clob")
                session.add(sr)
                session.flush()
                try:
                    book = client.fetch_order_books(market)
                    upstream = _upstream_dt(book.get("upstream_timestamp"))
                    if upstream and (utcnow() - upstream).total_seconds() > get_settings().app_stale_hours * 3600:
                        stale = True
                    save_raw_response(
                        session,
                        market,
                        "polymarket_clob",
                        f"{get_settings().polymarket_clob_base_url}/book",
                        book,
                        200,
                        upstream_timestamp=upstream,
                        stale=stale,
                    )
                    sr.status = "OK"
                    sr.items_discovered = 1
                    sr.items_inserted = 1
                except Exception as exc:
                    market_errors.append(_safe_error(exc))
                    sr.status = "FAILED"
                    sr.sanitized_error = _safe_error(exc)
                    stale = True
                    job.error_count += 1
                finally:
                    sr.finished_at = utcnow()

                evidence, inserted = _collect_market_evidence(session, job, market)
                job.evidence_discovered += inserted
                job.evidence_relevant += len(evidence)
                proposal = _maybe_create_proposal(session, market, evidence)
                proposals = [proposal] if proposal else []
                if proposal:
                    job.proposals_created += 1
                if book is None:
                    status = "FAILED"
                    message = "Market price unavailable. " + " | ".join(market_errors)
                elif market_errors:
                    status = "PARTIAL"
                    message = "Partial update. " + " | ".join(market_errors)
                elif evidence:
                    status = "OK"
                    message = f"{len(evidence)} relevant new evidence item(s); published fair value unchanged pending approval."
                else:
                    status = "OK"
                    message = "No material new evidence. Published fair value unchanged."
                _upsert_daily_snapshot(session, market, book, evidence, proposals, status, message, stale)
                job.snapshots_written += 1
                if status in {"OK", "PARTIAL"}:
                    job.markets_succeeded += 1
                session.flush()
                session.commit()

            _update_daily_index(session, day)
            job.status = "OK" if job.error_count == 0 else "PARTIAL"
        except Exception as exc:
            job.status = "FAILED"
            job.sanitized_error = _safe_error(exc)
            job.error_count += 1
            job.metadata_json = json.dumps({"trace": traceback.format_exc(limit=4)})
        finally:
            job.finished_at = utcnow()
            session.flush()
            digest_markdown: str | None = None
            digest_path: str | None = None
            try:
                digest = write_daily_digest(session, job, day)
                digest_markdown = digest.markdown
                digest_path = str(digest.latest_path)
                metadata = json.loads(job.metadata_json or "{}")
                metadata["daily_digest"] = digest.summary
                metadata["daily_digest_path"] = digest_path
                metadata["daily_digest_markdown"] = digest.markdown
                job.metadata_json = json.dumps(metadata, default=str)
            except Exception as digest_exc:
                metadata = json.loads(job.metadata_json or "{}")
                metadata["digest_error"] = _safe_error(digest_exc)
                job.metadata_json = json.dumps(metadata, default=str)
            session.commit()
            result = {
                "status": job.status,
                "run_key": run_key,
                "job_run_id": job.id,
                "markets_attempted": job.markets_attempted,
                "markets_succeeded": job.markets_succeeded,
                "evidence_discovered": job.evidence_discovered,
                "evidence_relevant": job.evidence_relevant,
                "proposals_created": job.proposals_created,
                "snapshots_written": job.snapshots_written,
                "error_count": job.error_count,
                "daily_digest_path": digest_path,
            }
        _send_discord_digest(job, digest_markdown)
        return result
