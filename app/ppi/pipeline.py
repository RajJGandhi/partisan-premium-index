from __future__ import annotations

import json
import traceback
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import select
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
from app.ppi.blind_forecast import (
    AUTOMATED_PROVIDERS,
    compute_and_persist_blind_index,
    generate_blind_forecast,
    is_matched_pair,
    join_forecast_with_price,
    qwen_provider_config,
)
from app.ppi.digest import write_daily_digest
from app.ppi.evidence import adapter_for, insert_and_classify_candidate
from app.ppi.experiment_metadata import record_first_eligible_observation
from app.ppi.job_run_lifecycle import derive_run_key, open_run
from app.ppi.lock import PipelineLockedError, pipeline_lock
from app.ppi.methodology import DEFAULT_WEIGHTS, compute_weighted_fair_value, partisan_premium
from app.ppi.polymarket import TrackedPolymarketClient, price_policy, save_raw_response, update_market_from_gamma
from app.ppi.run_classification import compute_run_classification


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


def _collect_market_evidence(
    session: Session, job: JobRun, market: Market, *, strict: bool = False
) -> tuple[list[EvidenceItem], int]:
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
                item, inserted = insert_and_classify_candidate(session, market, source, candidate, strict=strict)
                if inserted:
                    inserted_count += 1
                    sr.items_inserted += 1
                    if item.review_status == "CLASSIFICATION_FAILED":
                        job.evidence_classification_failed += 1
                    elif item.relevant:
                        relevant_new.append(item)
                    if item.classifier_provider == "deterministic_fallback":
                        job.llm_fallback_count += 1
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
    trigger_type: str = "manual",
    force: bool = False,
    run_date: date | None = None,
    strict_llm_only: bool = False,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Public entry point: acquires the filesystem concurrency lock, then runs the pipeline.

    A locked pipeline (another run genuinely in progress) is a benign, expected outcome -- not a
    failure -- since the GitHub Actions ``concurrency:`` group is the primary defense and normally
    prevents this from ever being reached; this is only the backstop for an out-of-band
    invocation. It returns status "LOCKED" without touching any JobRun row.

    ``lock_path`` defaults to the real project-relative lock file; tests should pass a
    ``tmp_path``-scoped path instead of touching actual repository state.
    """
    day = run_date or utcnow().date()
    run_key = derive_run_key(trigger_type, day)
    try:
        with pipeline_lock(lock_path) if lock_path else pipeline_lock():
            return _run_daily_pipeline_locked(trigger_type, force, run_date, strict_llm_only)
    except PipelineLockedError as exc:
        return {"status": "LOCKED", "run_key": run_key, "error": str(exc)}


def _run_daily_pipeline_locked(
    trigger_type: str,
    force: bool,
    run_date: date | None,
    strict_llm_only: bool,
) -> dict[str, Any]:
    init_db()
    day = run_date or utcnow().date()
    run_key = derive_run_key(trigger_type, day)
    with get_session() as session:
        # One shared run record. If the workflow already opened this run_key as RUNNING (its
        # start step, before migrations / the provider check), open_run re-attaches to that same
        # row and preserves its started_at; otherwise it creates or resets one. This is also the
        # idempotency guarantee -- an already-OK slot short-circuits unless force is set.
        job, outcome = open_run(
            session,
            run_key=run_key,
            trigger_type=trigger_type,
            pipeline_mode="strict_llm_only" if strict_llm_only else "standard_mixed_fallback_allowed",
            force=force,
            now=utcnow(),
        )
        if outcome == "already_complete":
            return {"status": "ALREADY_COMPLETE", "run_key": run_key, "job_run_id": job.id}

        # Canonical blind-Qwen series requires a real model backend for every classification and
        # forecast. Refuse to run at all rather than silently producing SKIPPED_PROVIDER/fallback
        # results that would contaminate a run meant to be labelled "strict_llm_only".
        if strict_llm_only and get_settings().llm_provider not in AUTOMATED_PROVIDERS:
            job.status = "FAILED"
            job.sanitized_error = (
                f"strict_llm_only requires LLM_PROVIDER in {sorted(AUTOMATED_PROVIDERS)}; "
                f"got {get_settings().llm_provider!r}. Refusing to run to avoid producing "
                "fallback-derived results in a run labelled strict_llm_only."
            )
            job.finished_at = utcnow()
            job.run_classification = compute_run_classification(session, job, run_key)
            session.commit()
            return {
                "status": job.status,
                "run_key": run_key,
                "job_run_id": job.id,
                "error": job.sanitized_error,
            }
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
        matched_pair_observed = False
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

                evidence, inserted = _collect_market_evidence(session, job, market, strict=strict_llm_only)
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
                snap = _upsert_daily_snapshot(session, market, book, evidence, proposals, status, message, stale)
                job.snapshots_written += 1
                if status in {"OK", "PARTIAL"}:
                    job.markets_succeeded += 1

                # Primary blind-LLM (DeepSeek V4 Flash 0731, via OpenRouter) forecast series.
                # CUTOVER (2026-08-26): this was the Qwen/Ollama series until this date -- see
                # docs/research/DEEPSEEK_PRIMARY_CUTOVER_DEVIATION_20260826.md. Generated after
                # the human-approved weighted snapshot above, but the two never share inputs: the
                # blind forecast never sees `book`/`snap` price data. A SAVEPOINT isolates an
                # unexpected failure here so it cannot abort the rest of the run for other markets.
                # One timestamp for both arms of this run so they share a run_slot (the twice-daily
                # slot is derived from `forecast_now`); without this a wall-clock second boundary
                # between the two calls could split them into different adhoc slots.
                forecast_now = utcnow()
                job.llm_forecasts_attempted += 1
                deepseek_forecast = None
                try:
                    with session.begin_nested():
                        deepseek_forecast = generate_blind_forecast(
                            session,
                            market,
                            job=job,
                            run_key=run_key,
                            trigger_type=trigger_type,
                            day=day,
                            now=forecast_now,
                            strict=strict_llm_only,
                        )
                    if deepseek_forecast.status == "OK":
                        job.llm_forecasts_succeeded += 1
                    elif deepseek_forecast.status == "ABSTAINED":
                        job.llm_forecasts_abstained += 1
                    elif deepseek_forecast.status == "SKIPPED_PROVIDER":
                        job.llm_forecasts_skipped += 1
                    else:
                        job.llm_forecasts_failed += 1
                        job.error_count += 1
                except Exception as exc:
                    job.llm_forecasts_failed += 1
                    job.error_count += 1
                    print(f"[ppi] blind forecast failed for market_id={market.id}: {_safe_error(exc)}")

                # Secondary comparison arm (Qwen3-8B, local Ollama) -- retained post-cutover so
                # the matched Qwen-vs-DeepSeek comparison data (preregistered in
                # docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md) keeps accumulating even
                # though DeepSeek is now primary. Called from the exact same evidence: no
                # evidence-discovery step runs between this call and the DeepSeek call above, so
                # build_blind_evidence_packet (invoked fresh inside generate_blind_forecast) reads
                # byte-identical EvidenceItem rows both times, which is exactly why both rows'
                # prompt_hash values are directly comparable below. This never blocks, gates, or
                # falls back to/from the canonical DeepSeek call -- a comparison-arm failure here
                # affects only this row. Not counted in job.llm_forecasts_* (those remain
                # exclusively about the primary series' health, consistent with
                # app.ppi.run_health/app.ppi.run_classification's own scoping); Qwen's outcomes
                # are queryable directly from its own LLMForecast rows.
                qwen_forecast = None
                try:
                    with session.begin_nested():
                        qwen_forecast = generate_blind_forecast(
                            session,
                            market,
                            job=job,
                            run_key=run_key,
                            trigger_type=trigger_type,
                            day=day,
                            now=forecast_now,
                            strict=strict_llm_only,
                            provider_config=qwen_provider_config(get_settings()),
                        )
                except Exception as exc:
                    print(f"[ppi] qwen comparison forecast failed for market_id={market.id}: {_safe_error(exc)}")

                if qwen_forecast is not None and deepseek_forecast is not None:
                    if is_matched_pair(qwen_forecast, deepseek_forecast):
                        matched_pair_observed = True
                    elif qwen_forecast.status == "OK" and deepseek_forecast.status == "OK":
                        # Should be structurally impossible given the sequencing guarantee above
                        # -- both calls read the same EvidenceItem rows with nothing in between
                        # that could insert new evidence. Treated as a hard integrity signal for
                        # this pair specifically, not a run-wide failure: both rows are still
                        # persisted exactly as generated, just not usable as a matched
                        # observation.
                        print(
                            f"[ppi] WARNING evidence-hash mismatch for market_id={market.id}: "
                            f"qwen prompt_hash={qwen_forecast.prompt_hash!r} "
                            f"deepseek prompt_hash={deepseek_forecast.prompt_hash!r}"
                        )

                # Price/benchmark joining happens only after BOTH forecast attempts have
                # terminated (preregistration Section 6, step 7) -- never before either model has
                # committed its own forecast row, and never in a way either model could see.
                if qwen_forecast is not None:
                    try:
                        with session.begin_nested():
                            join_forecast_with_price(session, qwen_forecast, snap)
                    except Exception as exc:
                        print(f"[ppi] price join failed (qwen) for market_id={market.id}: {_safe_error(exc)}")
                if deepseek_forecast is not None:
                    try:
                        with session.begin_nested():
                            join_forecast_with_price(session, deepseek_forecast, snap)
                    except Exception as exc:
                        print(f"[ppi] price join failed (deepseek) for market_id={market.id}: {_safe_error(exc)}")

                session.flush()
                session.commit()

            if matched_pair_observed:
                try:
                    record_first_eligible_observation(session, job_run_id=job.id, observed_at=utcnow())
                    session.commit()
                except Exception as exc:
                    print(f"[ppi] failed to record first-eligible-observation marker: {_safe_error(exc)}")

            _update_daily_index(session, day)
            compute_and_persist_blind_index(session, job, run_key)
            job.status = "OK" if job.error_count == 0 else "PARTIAL"
        except Exception as exc:
            job.status = "FAILED"
            job.sanitized_error = _safe_error(exc)
            job.error_count += 1
            job.metadata_json = json.dumps({"trace": traceback.format_exc(limit=4)})
        finally:
            job.finished_at = utcnow()
            job.run_classification = compute_run_classification(session, job, run_key)
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
