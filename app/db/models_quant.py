"""PPI Quant v1.5 schema -- the new deterministic-forecast + ensemble + provenance tables.

Additive only. Nothing here modifies or replaces an existing table; the legacy blind-LLM series
(``llm_forecasts``) is retained unchanged and merely gains two labelling columns via
``scripts/migrate_db.py`` (``methodology_version`` defaulting to ``ppi-v0-legacy-blind-llm`` and
``forecast_role`` defaulting to ``legacy_blind_llm``).

Immutability model (spec sections 3, 32):

* ``quant_forecasts`` / ``ensemble_forecasts`` are **append-only per natural key**. A re-run of the
  same logical slot is a no-op if a row already exists; a genuine correction inserts a *new* row
  with ``correction_of_id`` set and ``revision`` incremented -- the original is never edited.
* ``quant_evidence_bundles`` rows are write-once (``content_hash`` unique).
* ``poll_observations`` / ``national_environment_observations`` dedup on ``content_hash`` and are
  never mutated after insert.
* ``methodology_versions`` is write-once per ``version``.

See ``app/quant/append_only.py`` for the runtime guard used by the shadow runner and pipeline.
"""

from __future__ import annotations

from datetime import date, datetime
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.models import utcnow


class Race(Base):
    """Canonical, provider-independent political race identity (spec section 7)."""

    __tablename__ = "races"
    __table_args__ = (
        UniqueConstraint("race_id", name="uq_race_race_id"),
        Index("ix_races_cycle_office", "cycle", "office"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)  # "nc-sen-2026"
    state: Mapped[str] = mapped_column(String(2), index=True)
    office: Mapped[str] = mapped_column(String(20), index=True)  # senate / governor
    cycle: Mapped[int] = mapped_column(Integer, index=True)
    election_date: Mapped[date] = mapped_column(Date)
    adapter_type: Mapped[str] = mapped_column(String(40), default="statewide_race")
    # Loose link to a Polymarket market (markets.id); nullable so a race can exist without a contract.
    polymarket_market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    polymarket_market_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dem_candidate_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rep_candidate_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    incumbent_party: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    # Which party winning = the canonical PPI race contract resolving YES. Default DEM: the Quant
    # p_dem_win, the blind prompt ("Will the Democratic candidate win..."), and the outcome
    # (resolutions.dem_won) are all already in this orientation.
    contract_yes_party: Mapped[str] = mapped_column(String(10), default="DEM")
    # Which party the *linked Polymarket contract's* YES side names (set during market discovery).
    # NULL -> the market + legacy-LLM series cannot be safely oriented to contract_yes_party and
    # are excluded from scoring for this race (abstain rather than guess a direction).
    market_yes_party: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", index=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RaceCandidate(Base):
    __tablename__ = "race_candidates"
    __table_args__ = (
        UniqueConstraint("race_id", "normalized_name", name="uq_race_candidate_name"),
        Index("ix_race_candidates_race", "race_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    party: Mapped[str] = mapped_column(String(10))  # DEM / REP / OTHER
    is_incumbent: Mapped[bool] = mapped_column(Boolean, default=False)
    candidate_status: Mapped[str] = mapped_column(String(30), default="confirmed")
    fec_candidate_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    committee_ids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PollObservation(Base):
    """One normalized individual race poll (spec section 6). Append-only; dedup on content_hash."""

    __tablename__ = "poll_observations"
    __table_args__ = (
        UniqueConstraint("race_id", "content_hash", name="uq_poll_obs_race_hash"),
        Index("ix_poll_obs_race_end", "race_id", "end_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    provider_poll_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    pollster: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    population: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # LV / RV / A
    pollster_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    partisan_sponsor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    dem_candidate: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rep_candidate: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    dem_pct: Mapped[float] = mapped_column(Float)
    rep_pct: Mapped[float] = mapped_column(Float)
    margin_dem: Mapped[float] = mapped_column(Float)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    normalized_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(20), default="OK")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("data_provider_runs.id", ondelete="SET NULL"), nullable=True
    )


class NationalEnvironmentObservation(Base):
    """One generic-congressional-ballot poll (spec section 10). Append-only; dedup on content_hash."""

    __tablename__ = "national_environment_observations"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_natenv_obs_hash"),
        Index("ix_natenv_obs_end", "end_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    pollster: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    population: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    pollster_grade: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    partisan_sponsor: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    dem_pct: Mapped[float] = mapped_column(Float)
    rep_pct: Mapped[float] = mapped_column(Float)
    margin_dem: Mapped[float] = mapped_column(Float)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(20), default="OK")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("data_provider_runs.id", ondelete="SET NULL"), nullable=True
    )


class HistoricalElectionResult(Base):
    """State + national presidential baselines for state-lean (spec section 9). Rarely changes;
    upserted per (jurisdiction, year, office)."""

    __tablename__ = "historical_election_results"
    __table_args__ = (
        UniqueConstraint("jurisdiction", "year", "office", name="uq_hist_result_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jurisdiction: Mapped[str] = mapped_column(String(20), index=True)  # "US" or 2-letter state
    year: Mapped[int] = mapped_column(Integer, index=True)
    office: Mapped[str] = mapped_column(String(20), default="president")
    dem_votes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rep_votes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_votes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dem_margin_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Dem - Rep, points
    provider: Mapped[str] = mapped_column(String(50), default="seed")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CandidateStatusSnapshot(Base):
    """Point-in-time incumbency / nominee status (spec section 8). Append-only."""

    __tablename__ = "candidate_status_snapshots"
    __table_args__ = (Index("ix_cand_status_race_time", "race_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    dem_candidate: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rep_candidate: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    incumbent_party: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    open_seat: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    nominees_confirmed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    mapping_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="seed")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProviderCache(Base):
    """Response cache + last-known-good store for the provider layer (spec sections 5, 31).

    ``cache_key`` = sha256 of (provider + method + url + sorted params). A fresh row within TTL is
    served as a cache hit; the newest ``ok`` row for an ``endpoint_family`` is the last-known-good
    fallback when every live attempt fails. Append-only: a new fetch inserts a new row, never edits.
    """

    __tablename__ = "provider_cache"
    __table_args__ = (
        Index("ix_provider_cache_key_time", "cache_key", "fetched_at"),
        Index("ix_provider_cache_family_time", "provider_name", "endpoint_family", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    provider_name: Mapped[str] = mapped_column(String(60), index=True)
    endpoint_family: Mapped[str] = mapped_column(String(80), index=True)  # e.g. "ddhq:ballot_test"
    url: Mapped[str] = mapped_column(Text)
    request_params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MarketClassification(Base):
    """Automatic Polymarket market discovery + classification (spec section 42).

    One row per discovered political contract. ``status`` is ACCEPTED (auto-published: mapped onto
    a supported adapter with confidence above the threshold and bound to a ``races`` row) or
    QUARANTINED (ambiguous / low-confidence / unsupported -- shown with market price + blind LLM
    only, never a fabricated Quant forecast). Append-only; re-classification of the same contract
    appends a new row.
    """

    __tablename__ = "market_classifications"
    __table_args__ = (
        Index("ix_market_classification_ref_time", "market_ref", "classified_at"),
        Index("ix_market_classification_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_ref: Mapped[str] = mapped_column(String(255), index=True)  # gamma id or slug
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), index=True)  # SUPPORTED_STATEWIDE_RACE / ... / AMBIGUOUS
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20), default="deterministic")  # deterministic / llm
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    race_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    race_hint_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="QUARANTINED", index=True)  # ACCEPTED / QUARANTINED
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )


class DataProviderRun(Base):
    """Every ingestion operation, with the provider actually used and any fallback (spec section 31)."""

    __tablename__ = "data_provider_runs"
    __table_args__ = (Index("ix_provider_runs_kind_started", "provider_kind", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_kind: Mapped[str] = mapped_column(String(40), index=True)  # poll / election_history / ...
    provider_requested: Mapped[str] = mapped_column(String(60))
    provider_used: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    fallback_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)  # race_id / "generic_ballot"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")  # OK / STALE / FAILED / EMPTY
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    items_ingested: Mapped[int] = mapped_column(Integer, default=0)
    used_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    used_last_known_good: Mapped[bool] = mapped_column(Boolean, default=False)
    response_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sanitized_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class QuantEvidenceBundle(Base):
    """Immutable, timestamp-locked, market-free forecast inputs (spec section 22). Write-once."""

    __tablename__ = "quant_evidence_bundles"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_quant_evidence_hash"),
        Index("ix_quant_evidence_race_time", "race_id", "forecast_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    forecast_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    election_date: Mapped[date] = mapped_column(Date)
    evidence_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_manifest_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuantForecast(Base):
    """Deterministic PPI Quant output (spec section 32). Append-only per (race_id, run_key,
    methodology_version); a correction is a new row with ``correction_of_id`` set."""

    __tablename__ = "quant_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "race_id", "run_key", "methodology_version", "revision", name="uq_quant_forecast_slot"
        ),
        Index("ix_quant_forecast_race_generated", "race_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    run_slot: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    adapter_type: Mapped[str] = mapped_column(String(40), default="statewide_race")
    methodology_version: Mapped[str] = mapped_column(String(40), index=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    evidence_bundle_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quant_evidence_bundles.id", ondelete="SET NULL"), nullable=True
    )
    evidence_bundle_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    evidence_cutoff: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    data_quality: Mapped[str] = mapped_column(String(20), index=True)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    abstain_reasons_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    polling_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fundamental_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    poll_weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # alpha
    expected_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # mu

    sigma_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sigma_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sigma_polling: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sigma_office: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sigma_status: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    p_dem_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # capped, published
    p_dem_win_uncapped: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    p_rep_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Dem-outcome-oriented fair value for the specific contract (may be p_dem_win or p_rep_win
    # depending on which party the YES side names). NULL until bound to a contract.
    fair_value_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    n_eff: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    used_poll_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_poll_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    state_lean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    national_environment: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    incumbency_points: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pipeline_mode: Mapped[str] = mapped_column(String(30), default="shadow", index=True)  # shadow / canonical
    publication_status: Mapped[str] = mapped_column(String(20), default="SHADOW", index=True)
    integrity_flag: Mapped[str] = mapped_column(String(20), default="NONE", index=True)  # NONE / FLAGGED
    integrity_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    correction_of_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quant_forecasts.id", ondelete="SET NULL"), nullable=True
    )


class EnsembleForecast(Base):
    """PPI Ensemble = 0.60 Quant + 0.20 GPT + 0.20 Claude (spec section 25). Append-only per slot.

    ``available = False`` records an explicit "ensemble unavailable" -- components are never
    silently reweighted when one is missing."""

    __tablename__ = "ensemble_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "race_id", "run_key", "methodology_version", "revision", name="uq_ensemble_forecast_slot"
        ),
        Index("ix_ensemble_forecast_race_generated", "race_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    methodology_version: Mapped[str] = mapped_column(String(40), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    available: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    unavailable_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    quant_forecast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quant_forecasts.id", ondelete="SET NULL"), nullable=True
    )
    quant_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    openai_forecast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("blind_benchmark_forecasts.id", ondelete="SET NULL"), nullable=True
    )
    openai_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    anthropic_forecast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("blind_benchmark_forecasts.id", ondelete="SET NULL"), nullable=True
    )
    anthropic_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    weights_json: Mapped[str] = mapped_column(Text)
    ensemble_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dispersion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_pairwise_disagreement: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    robustness: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, index=True)

    pipeline_mode: Mapped[str] = mapped_column(String(30), default="shadow", index=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="SHADOW", index=True)
    integrity_flag: Mapped[str] = mapped_column(String(20), default="NONE", index=True)
    integrity_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    correction_of_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ensemble_forecasts.id", ondelete="SET NULL"), nullable=True
    )


class BlindBenchmarkForecast(Base):
    """Independent blind-LLM benchmark forecast -- GPT (spec section 23) or Claude (spec section 24).

    Race-centric, append-only per (race_id, run_key, provider, methodology_version, revision), kept
    entirely separate from the legacy market-centric ``llm_forecasts`` table (which is unchanged).
    The model receives only the timestamp-locked, market-free ``EvidenceBundle``; it never sees a
    prediction-market price, the Quant probability, the *other* model's forecast, or the ensemble.
    A missing key / SDK yields an explicit ``SKIPPED_PROVIDER`` row -- never a fabricated value or a
    silent substitution.
    """

    __tablename__ = "blind_benchmark_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "race_id", "run_key", "provider", "methodology_version", "revision",
            name="uq_blind_benchmark_slot",
        ),
        Index("ix_blind_benchmark_race_generated", "race_id", "generated_at"),
        Index("ix_blind_benchmark_provider", "provider", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    job_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    run_slot: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)  # openai / anthropic
    model_name: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(40))
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    methodology_version: Mapped[str] = mapped_column(String(40), index=True)

    evidence_bundle_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quant_evidence_bundles.id", ondelete="SET NULL"), nullable=True
    )
    evidence_bundle_hash: Mapped[str] = mapped_column(String(64), index=True)
    evidence_cutoff: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # P(YES), NULL if failed/abstained
    should_abstain: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uncertainty_drivers_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_rate_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)  # OK/ABSTAINED/FAILED/SKIPPED_PROVIDER
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)

    raw_request_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    web_search_calls: Mapped[int] = mapped_column(Integer, default=0)
    generation_params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # FLAG-only data-integrity review (mirrors llm_forecasts / quant_forecasts). Never edits a value.
    reviewed_status: Mapped[str] = mapped_column(String(30), default="UNREVIEWED", index=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    pipeline_mode: Mapped[str] = mapped_column(String(30), default="shadow", index=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="SHADOW", index=True)
    integrity_flag: Mapped[str] = mapped_column(String(20), default="NONE", index=True)
    integrity_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    correction_of_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("blind_benchmark_forecasts.id", ondelete="SET NULL"), nullable=True
    )


class RaceNewsItem(Base):
    """Contamination-filtered web evidence for a race (spec sections 20, 21, 45).

    Displayed + stored + available to the blind LLM benchmarks + available for integrity/status
    detection. **Not** an input to the deterministic Quant model (v1 Quant does not let qualitative
    news move the probability). Append-only; dedup on content_hash. A document that fails the
    prediction-market contamination scan is stored with its status and excluded from blind forecasts.
    """

    __tablename__ = "race_news_items"
    __table_args__ = (
        UniqueConstraint("race_id", "content_hash", name="uq_race_news_hash"),
        Index("ix_race_news_race_published", "race_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)  # openai_web_search / anthropic_web_search / manual
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_domain: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # withdrawal / legal / endorsement / ...
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    contamination_status: Mapped[str] = mapped_column(String(20), default="CLEAN", index=True)  # CLEAN/QUARANTINED/BLOCKED
    contamination_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blocked_source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ForecastMarketComparison(Base):
    """Join of a stored forecast fair value with a market snapshot, computed strictly *after* the
    forecast is persisted (spec sections 18, 19). ``market_model_spread = market - fair_value``."""

    __tablename__ = "forecast_market_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "race_id", "run_key", "series", "market_snapshot_id", name="uq_fmc_slot"
        ),
        Index("ix_fmc_race_observed", "race_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    market_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("markets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(150), index=True)
    series: Mapped[str] = mapped_column(String(20), index=True)  # quant / ensemble / openai / anthropic / legacy_llm
    quant_forecast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quant_forecasts.id", ondelete="SET NULL"), nullable=True
    )
    ensemble_forecast_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ensemble_forecasts.id", ondelete="SET NULL"), nullable=True
    )
    market_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    fair_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quote_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # midpoint / last_trade
    market_model_spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    abs_spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    robustness: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class ForecastResolution(Base):
    """Final race outcome, for scoring (spec section 32)."""

    __tablename__ = "forecast_resolutions"
    __table_args__ = (UniqueConstraint("race_id", name="uq_forecast_resolution_race"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    dem_won: Mapped[float] = mapped_column(Float)  # 1.0 if the Democratic candidate won, else 0.0
    final_margin_dem: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ForecastScore(Base):
    """Brier / calibration for each forecasting series at a standard horizon (spec sections 34-35)."""

    __tablename__ = "forecast_scores"
    __table_args__ = (
        UniqueConstraint("race_id", "series", "horizon_days", name="uq_forecast_score_slot"),
        Index("ix_forecast_score_series_horizon", "series", "horizon_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[str] = mapped_column(String(64), index=True)
    series: Mapped[str] = mapped_column(String(20), index=True)  # market / quant / openai / anthropic / ensemble / legacy_llm
    horizon_days: Mapped[int] = mapped_column(Integer, index=True)  # 90 / 60 / 30 / 14 / 7 / 1
    forecast_probability: Mapped[float] = mapped_column(Float)
    outcome: Mapped[float] = mapped_column(Float)
    brier_score: Mapped[float] = mapped_column(Float)
    log_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    forecast_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    methodology_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MethodologyVersion(Base):
    """Write-once snapshot of a versioned methodology config (spec section 33)."""

    __tablename__ = "methodology_versions"
    __table_args__ = (UniqueConstraint("version", name="uq_methodology_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="quant")  # quant / ensemble
    config_json: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderHealth(Base):
    """Rolling health for each external data provider (spec section 40)."""

    __tablename__ = "provider_health"
    __table_args__ = (UniqueConstraint("provider_name", name="uq_provider_health_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(60), index=True)
    provider_kind: Mapped[str] = mapped_column(String(40), index=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_data_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")  # HEALTHY / DEGRADED / DOWN / UNKNOWN
    recent_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
