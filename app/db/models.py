from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        UniqueConstraint("platform", "platform_market_id", name="uq_market_platform_id"),
        UniqueConstraint("tracking_id", name="uq_market_tracking_id"),
        Index("ix_markets_enabled_active", "enabled", "active", "closed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracking_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(50), default="polymarket", index=True)
    platform_market_id: Mapped[str] = mapped_column(String(255), index=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    condition_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    slug: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcomes_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clob_token_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    yes_token_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    no_token_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="TRACKED", index=True)
    enable_order_book: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_pack_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preferred_domains_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_thesis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    methodology_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    polling_weight: Mapped[float] = mapped_column(Float, default=0.35)
    forecast_weight: Mapped[float] = mapped_column(Float, default=0.25)
    comparable_weight: Mapped[float] = mapped_column(Float, default=0.20)
    expert_weight: Mapped[float] = mapped_column(Float, default=0.10)
    news_weight: Mapped[float] = mapped_column(Float, default=0.10)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_market_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_evidence_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    snapshots: Mapped[list[MarketSnapshot]] = relationship(back_populates="market")


class MarketSource(Base):
    __tablename__ = "market_sources"
    __table_args__ = (
        UniqueConstraint("market_id", "source_type", "name", name="uq_market_source_name"),
        Index("ix_market_sources_enabled", "market_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(50), index=True)  # rss, gdelt, json_api, manual, external_market
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    allowed_domains_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        # Run-aware identity: one canonical snapshot per (market, run), NOT per (market, date).
        # A twice-daily schedule produces two runs on the same calendar day -- both must survive,
        # so the 21:00 run cannot upsert over the 09:00 one. `run_key` is NULL for legacy rows and
        # for the non-canonical intraday / market_price_only kinds; NULLs are distinct in both
        # SQLite and PostgreSQL, so those rows are unconstrained (as before). See
        # scripts/migrate_db.py::_make_history_run_aware and migrations/002_run_aware_history.sql.
        UniqueConstraint("market_id", "run_key", name="uq_market_snapshot_run"),
        Index("ix_market_snapshots_market_date", "market_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # `snapshot_date` still records which observation day this belongs to (kept for range
    # queries and reporting) but no longer *identifies* the row. `timestamp` is the real
    # observation instant; `run_key` / `job_run_id` / `trigger_type` tie it to its pipeline run.
    snapshot_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    snapshot_kind: Mapped[str] = mapped_column(String(30), default="intraday")
    run_key: Mapped[Optional[str]] = mapped_column(String(150), nullable=True, index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trigger_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # primary | backup | adhoc
    yes_price_displayed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_price_displayed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_best_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_best_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_best_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_best_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_midpoint: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_midpoint: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_trade_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    comparison_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    executable_buy_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    executable_sell_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_1c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_3c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_5c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    partisan_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    component_inputs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    component_weights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effective_weights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pending_proposal_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    freshness_status: Mapped[str] = mapped_column(String(30), default="UNKNOWN", index=True)
    pipeline_status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    raw_orderbook_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    market: Mapped[Market] = relationship(back_populates="snapshots")


class RawMarketResponse(Base):
    __tablename__ = "raw_market_responses"
    __table_args__ = (Index("ix_raw_market_source_time", "source", "fetched_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(100), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    upstream_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    request_params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        UniqueConstraint("market_id", "content_hash", name="uq_evidence_market_hash"),
        Index("ix_evidence_market_published", "market_id", "published_at"),
        Index("ix_evidence_review", "review_status", "relevant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    market_source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("market_sources.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_title: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relevant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_quality: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    changes_probability: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    direction: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    estimated_magnitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    classifier_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    classifier_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    classifier_raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class FairValue(Base):
    __tablename__ = "fair_values"
    __table_args__ = (UniqueConstraint("market_id", name="uq_fair_value_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    polling_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    forecast_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    other_markets_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expert_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    news_campaign_prob: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    manual_fair_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    computed_fair_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    published_fair_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FairValueComponent(Base):
    __tablename__ = "fair_value_components"
    __table_args__ = (
        UniqueConstraint("market_id", "component_type", name="uq_market_component"),
        Index("ix_fv_component_market", "market_id", "component_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    component_type: Mapped[str] = mapped_column(String(40))
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float)
    eligible_for_redistribution: Mapped[bool] = mapped_column(Boolean, default=True)
    source_label: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ingestion_method: Mapped[str] = mapped_column(String(30), default="manual")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FairValueProposal(Base):
    __tablename__ = "fair_value_proposals"
    __table_args__ = (Index("ix_fv_proposals_queue", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    proposed_fair_value: Mapped[float] = mapped_column(Float)
    current_published_fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    proposed_components_json: Mapped[str] = mapped_column(Text)
    proposed_weights_json: Mapped[str] = mapped_column(Text)
    effective_weights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(100), default="pipeline")
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class FairValueRevision(Base):
    __tablename__ = "fair_value_revisions"
    __table_args__ = (Index("ix_fv_revision_market_time", "market_id", "published_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fair_value_proposals.id", ondelete="SET NULL"), nullable=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    fair_value: Mapped[float] = mapped_column(Float)
    previous_fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    components_json: Mapped[str] = mapped_column(Text)
    weights_json: Mapped[str] = mapped_column(Text)
    effective_weights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thesis: Mapped[str] = mapped_column(Text)
    justification: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    published_by: Mapped[str] = mapped_column(String(100), default="admin")
    correction_of_revision_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("fair_value_revisions.id"), nullable=True
    )


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("market_id", "initial_revision_id", name="uq_prediction_initial_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    initial_revision_id: Mapped[int] = mapped_column(ForeignKey("fair_value_revisions.id"), index=True)
    initial_publication_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    initial_fair_value: Mapped[float] = mapped_column(Float)
    initial_market_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    initial_thesis: Mapped[str] = mapped_column(Text)
    supporting_evidence_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", index=True)
    final_outcome: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ppi_brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    performance_difference: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class MarketResolution(Base):
    __tablename__ = "market_resolutions"
    __table_args__ = (UniqueConstraint("market_id", name="uq_market_resolution"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    resolved_outcome: Mapped[float] = mapped_column(Float)
    resolved_label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(100), default="admin")


class DailyIndex(Base):
    __tablename__ = "daily_index"
    # Run-aware identity: one aggregate index observation per canonical run, NOT per calendar
    # day. `index_date` is retained as the semantic observation day but no longer unique --
    # a twice-daily schedule writes two rows for the same `index_date`, distinguished by
    # `run_key` / `trigger_type` / `generated_at`. (Table name stays `daily_index`: it is an
    # internal legacy name; the public series is exported as "index_history".)
    __table_args__ = (UniqueConstraint("run_key", name="uq_daily_index_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    index_date: Mapped[date] = mapped_column(Date, index=True)
    run_key: Mapped[Optional[str]] = mapped_column(String(150), nullable=True, index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trigger_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # primary | backup | adhoc
    tracked_market_count: Mapped[int] = mapped_column(Integer, default=0)
    fresh_market_count: Mapped[int] = mapped_column(Integer, default=0)
    average_signed_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_absolute_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity_weighted_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    share_above_fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    methodology_label: Mapped[str] = mapped_column(String(100), default="provisional_equal_weight")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(30), default="OK")


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_name_started", "job_name", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    trigger_type: Mapped[str] = mapped_column(String(30), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING", index=True)
    markets_attempted: Mapped[int] = mapped_column(Integer, default=0)
    markets_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    evidence_discovered: Mapped[int] = mapped_column(Integer, default=0)
    evidence_relevant: Mapped[int] = mapped_column(Integer, default=0)
    proposals_created: Mapped[int] = mapped_column(Integer, default=0)
    snapshots_written: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    sanitized_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_forecasts_attempted: Mapped[int] = mapped_column(Integer, default=0)
    llm_forecasts_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    llm_forecasts_abstained: Mapped[int] = mapped_column(Integer, default=0)
    llm_forecasts_failed: Mapped[int] = mapped_column(Integer, default=0)
    llm_forecasts_skipped: Mapped[int] = mapped_column(Integer, default=0)
    evidence_classification_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Evidence items classified via classify_with_fallback's silent deterministic degrade
    # (standard_mixed_fallback_allowed mode only -- strict_llm_only has no fallback path for
    # either evidence classification or forecast generation, so this is always 0 on a canonical
    # run by construction). See app.ppi.run_health for the derived observability this feeds.
    llm_fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    # "standard_mixed_fallback_allowed": evidence classification may silently degrade to the
    # deterministic classifier on an LLM failure (legacy/default behavior; this is what every run
    # before this column existed used). "strict_llm_only": the canonical blind-Qwen series -- a
    # classification or forecast failure is recorded explicitly and never masked by a fallback.
    pipeline_mode: Mapped[str] = mapped_column(String(40), default="standard_mixed_fallback_allowed", index=True)
    # One of: canonical, noncanonical_mixed, contaminated, failed, adhoc. Computed automatically
    # at the end of the run (see app.ppi.run_classification.compute_run_classification) from
    # pipeline_mode, status, trigger_type, and whether any forecast in this run pulled in
    # non-live-classified evidence. Never hand-edited.
    run_classification: Mapped[str] = mapped_column(String(30), default="adhoc", index=True)
    # Set explicitly (never auto-computed) when a later run replaces this one's results for
    # public/reporting purposes without deleting it -- e.g. a contaminated run redone cleanly
    # under a different run_key. NULL means not superseded.
    superseded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # End-to-end lifecycle observability (app.ppi.job_run_lifecycle). The row is opened as
    # RUNNING by the workflow itself -- before migrations / provider checks / the pipeline --
    # so a failure in any prerequisite step still leaves an auditable FAILED row instead of no
    # row at all. These three are set from GitHub Actions context and never contain a secret.
    #   workflow_run_id: "<github.run_id>/<github.run_attempt>" (or a manual label). Null for
    #     purely local runs.
    #   git_sha: the commit the attempt ran against.
    #   error_stage: the coarse pipeline stage that failed (e.g. "provider_check", "migrate_db",
    #     "run_pipeline", "deploy"). Null on success. A slug, never raw error text.
    workflow_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    git_sha: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_stage: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (Index("ix_source_runs_job_source", "job_run_id", "source_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    market_source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("market_sources.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    items_discovered: Mapped[int] = mapped_column(Integer, default=0)
    items_inserted: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sanitized_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LLMForecast(Base):
    """Blind-LLM fair-value forecast series (primary DeepSeek/OpenRouter series -- Qwen/Ollama
    prior to the 2026-08-26 cutover, see PRIMARY_SERIES_PROVIDERS -- plus any separately-labelled
    comparison model series, distinguished by ``model_provider``).

    Append-only per (market_id, run_slot, model_provider): a scheduled run twice per day produces
    at most one row per market per slot per provider -- so a second, independent model series
    (e.g. ``ollama``) can never overwrite or be silently skipped in favor of the primary
    ``openrouter`` series' row for the same market/slot, and vice versa. A row that reached status OK
    is never overwritten by a later call for the same (market, slot, provider) -- only a still-
    pending/failed one may be retried in place. Corrections to a genuinely wrong forecast require
    a new run_slot, never an edit of this table.
    """

    __tablename__ = "llm_forecasts"
    __table_args__ = (
        UniqueConstraint("market_id", "run_slot", "model_provider", name="uq_llm_forecast_market_run_slot_provider"),
        Index("ix_llm_forecasts_market_generated", "market_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="CASCADE"), index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    run_slot: Mapped[str] = mapped_column(String(60), index=True)
    trigger_type: Mapped[str] = mapped_column(String(30), default="manual")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model_provider: Mapped[str] = mapped_column(String(30))
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(30))
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    generation_params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Whether every evidence item shown to the model was itself classified by a live model, not a
    # deterministic-fallback or failed-classification item left over from a different (possibly
    # non-strict) run reused via content-hash dedup. Only meaningful for strict_llm_only runs;
    # None for standard-mode forecasts. Drives the "contaminated" run classification.
    evidence_all_live_classified: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    should_abstain: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_uncertainties_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_rate_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    comparison_price_at_join: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_ppi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Publication review layer: humans may approve a valid forecast for public display, or flag a
    # data-quality concern, but this must never touch fair_value/confidence/rationale/etc above --
    # the numeric primary-model forecast is edited by nobody, ever.
    reviewed_status: Mapped[str] = mapped_column(String(30), default="UNREVIEWED", index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # PPI Quant v1.5 labelling: the pre-rewrite blind-LLM series is retained unchanged and stamped
    # as the legacy series so it can never be confused with the new deterministic Quant series or
    # the new independent GPT/Claude blind-benchmark series. See docs/research/PPI_QUANT_V1.md.
    # ``forecast_role``: legacy_blind_llm | gpt_blind_benchmark | claude_blind_benchmark
    methodology_version: Mapped[str] = mapped_column(
        String(40), default="ppi-v0-legacy-blind-llm", index=True
    )
    forecast_role: Mapped[str] = mapped_column(String(30), default="legacy_blind_llm", index=True)


class BlindIndexRun(Base):
    """Aggregate daily index for the primary blind-LLM (Qwen) series only.

    Structurally separate from ``DailyIndex``, which aggregates the legacy human-approved
    weighted series (``MarketSnapshot.partisan_premium``, sourced from admin-entered
    ``FairValueComponent`` rows). The two series must never be mixed into one table: this table
    exists specifically so the blind-Qwen aggregate is never confused with, or silently
    substituted by, the human-weighted one.

    Upserted per ``run_key`` (one row per canonical job run): rerunning the exact same scheduled
    window recomputes and overwrites this row's aggregate figures in place rather than appending
    a duplicate, since it is a derived summary of the (immutable) ``LLMForecast`` rows for that
    run_key, not a raw model observation itself.
    """

    __tablename__ = "blind_index_runs"
    __table_args__ = (UniqueConstraint("run_key", name="uq_blind_index_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run_id: Mapped[int] = mapped_column(ForeignKey("job_runs.id", ondelete="CASCADE"), index=True)
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    effective_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    market_count: Mapped[int] = mapped_column(Integer, default=0)
    average_signed_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_signed_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_absolute_premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(30))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# Existing research/paper-trading tables are preserved for backward compatibility.
class LLMClassification(Base):
    __tablename__ = "llm_classifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    model: Mapped[str] = mapped_column(String(100))
    market_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emotional_side: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ideological_coding: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    identity_intensity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    institutional_friction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deadline_decay_relevance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    classification_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ResolutionRisk(Base):
    __tablename__ = "resolution_risks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    model: Mapped[str] = mapped_column(String(100))
    resolution_risk: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ambiguous_terms_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_dependency: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    implementation_vs_announcement_risk: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    date_boundary_risk: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExternalMarketMapping(Base):
    __tablename__ = "external_market_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    platform: Mapped[str] = mapped_column(String(50))
    external_market_id: Mapped[str] = mapped_column(String(255))
    external_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    same_underlying_event: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    matching_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    safe_to_compare_prices: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    material_differences_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExternalMarketSnapshot(Base):
    __tablename__ = "external_market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mapping_id: Mapped[int] = mapped_column(ForeignKey("external_market_mappings.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    yes_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    midpoint: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_interest: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PPISignal(Base):
    __tablename__ = "ppi_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    polymarket_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fair_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    premium: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ppi_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    paper_side: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    score_breakdown_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("ppi_signals.id"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    side: Mapped[str] = mapped_column(String(20))
    entry_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entry_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_favorable_excursion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_adverse_excursion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open")
    resolution_result: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("ppi_signals.id"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    channel: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    sent_successfully: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class RawAPIResponse(Base):
    __tablename__ = "raw_api_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ExperimentMetadata(Base):
    """Append-only marker recording the first eligible observation of a preregistered experiment.

    Exactly one row per ``experiment_key``, written once, on the first canonical JobRun that
    produces at least one valid matched pair (see ``app.ppi.blind_forecast.is_matched_pair``) for
    that experiment. Never updated after that first row exists -- see
    ``docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md`` Section 1 ("Experiment start rule").
    """

    __tablename__ = "experiment_metadata"
    __table_args__ = (UniqueConstraint("experiment_key", name="uq_experiment_metadata_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    experiment_key: Mapped[str] = mapped_column(String(100), index=True)
    preregistration_commit_sha: Mapped[str] = mapped_column(String(40))
    implementation_commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    first_job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# PPI Quant v1.5 tables (deterministic forecast + ensemble + provider/provenance). Imported here so
# a single ``from app.db import models`` registers them on ``Base.metadata`` for create_all/migrate.
# Additive only -- see app/db/models_quant.py and docs/research/PPI_QUANT_V1.md.
from app.db import models_quant  # noqa: E402,F401
