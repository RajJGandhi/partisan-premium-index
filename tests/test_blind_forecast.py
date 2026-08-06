from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.database import Base
from app.db.models import EvidenceItem, LLMForecast, Market, MarketSnapshot
from app.ppi.blind_forecast import (
    FORBIDDEN_PACKET_KEYS,
    assert_blind_packet,
    build_blind_evidence_packet,
    build_prompt,
    determine_run_slot,
    generate_blind_forecast,
    join_forecast_with_price,
)


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'blind.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_market(session) -> Market:
    market = Market(
        platform_market_id="1",
        tracking_id="T-1",
        question="Will the test resolve YES?",
        rules="Resolves YES if the test passes.",
        enabled=True,
    )
    session.add(market)
    session.flush()
    return market


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_evidence_packet_never_contains_forbidden_price_fields(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = _make_market(session)
        session.add(
            EvidenceItem(
                market_id=market.id,
                source_type="rss",
                source_name="Example",
                title="Recent poll shows tight race",
                normalized_title="recent poll shows tight race",
                content_hash="hash1",
                content_text="A new poll shows a tight race.",
                relevant=True,
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.flush()
        packet, evidence_ids = build_blind_evidence_packet(session, market)

    assert evidence_ids
    assert_blind_packet(packet)  # must not raise

    # Defense in depth: prove the guard actually fires for a leaked field.
    poisoned = dict(packet)
    poisoned["comparison_price"] = 0.42
    with pytest.raises(ValueError):
        assert_blind_packet(poisoned)

    # And prove it catches leaks nested inside the evidence list too.
    nested = json.loads(json.dumps(packet))
    nested["evidence"].append({"title": "x", "yes_best_bid": 0.5})
    with pytest.raises(ValueError):
        assert_blind_packet(nested)


def test_forbidden_key_set_covers_all_orderbook_and_snapshot_price_fields():
    price_fields = {
        "yes_price_displayed",
        "no_price_displayed",
        "yes_best_bid",
        "yes_best_ask",
        "no_best_bid",
        "no_best_ask",
        "yes_midpoint",
        "no_midpoint",
        "last_trade_price",
        "comparison_price",
        "spread",
        "volume",
        "liquidity",
    }
    assert price_fields <= FORBIDDEN_PACKET_KEYS


def test_determine_run_slot_maps_to_primary_backup_and_adhoc():
    # Canonical schedule is 06:00/18:00 America/Toronto. In EDT (summer) that's 10:00/22:00 UTC --
    # the configured PRIMARY_RUN_HOUR_UTC/BACKUP_RUN_HOUR_UTC defaults.
    day = date(2026, 8, 5)
    primary = datetime(2026, 8, 5, 10, 30, tzinfo=timezone.utc)
    backup = datetime(2026, 8, 5, 22, 5, tzinfo=timezone.utc)
    adhoc = datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)

    assert determine_run_slot(day, primary) == "2026-08-05:primary"
    assert determine_run_slot(day, backup) == "2026-08-05:backup"
    assert determine_run_slot(day, adhoc) == "2026-08-05:adhoc-030000"


def test_determine_run_slot_absorbs_the_est_dst_shift():
    # In EST (winter), 06:00/18:00 America/Toronto is 11:00/23:00 UTC -- one hour later than the
    # EDT-equivalent configured hours. The +/-2h tolerance must still classify these correctly so
    # the same cron entries work year-round without maintenance across DST transitions.
    day = date(2026, 1, 15)
    primary_est = datetime(2026, 1, 15, 11, 17, tzinfo=timezone.utc)
    backup_est = datetime(2026, 1, 15, 23, 17, tzinfo=timezone.utc)

    assert determine_run_slot(day, primary_est) == "2026-01-15:primary"
    assert determine_run_slot(day, backup_est) == "2026-01-15:backup"


def test_deterministic_provider_records_explicit_skip_not_a_fabricated_value(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="deterministic")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="test-run",
            trigger_type="manual",
            day=date(2026, 8, 5),
            now=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        )

    assert forecast.status == "SKIPPED_PROVIDER"
    assert forecast.fair_value is None
    assert "deterministic" in forecast.error_message
    assert forecast.run_slot == "2026-08-05:primary"


def test_valid_ollama_response_persists_ok_and_join_computes_raw_ppi(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    captured_payloads = []

    def fake_post(url, json=None, timeout=None):
        captured_payloads.append(json)
        body = {
            "fair_value": 0.62,
            "confidence": 0.7,
            "should_abstain": False,
            "rationale_short": "Base rates favor this outcome.",
            "key_uncertainties": ["Polling volatility"],
            "base_rate_notes": "Historical base rate is around 60%.",
        }
        return _FakeResponse({"response": json_dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        snap = MarketSnapshot(
            market_id=market.id,
            snapshot_date=date(2026, 8, 5),
            snapshot_kind="daily",
            comparison_price=0.80,
        )
        session.add(snap)
        session.flush()

        forecast = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="test-run",
            trigger_type="manual",
            day=date(2026, 8, 5),
            now=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        )
        join_forecast_with_price(session, forecast, snap)

    assert forecast.status == "OK"
    assert forecast.fair_value == pytest.approx(0.62)
    assert forecast.raw_ppi == pytest.approx(0.80 - 0.62)
    assert forecast.comparison_price_at_join == pytest.approx(0.80)
    assert forecast.joined_at is not None

    # The prompt sent to the model must never contain the market price.
    for payload in captured_payloads:
        assert "0.8" not in payload["prompt"]
        for forbidden in ("comparison_price", "yes_best_bid", "best_ask", "midpoint"):
            assert forbidden not in payload["prompt"]


def test_failed_parse_records_failed_status_and_never_fabricates_a_value(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)
    monkeypatch.setattr("requests.post", lambda url, json=None, timeout=None: _FakeResponse({"response": "not json"}))

    with Session.begin() as session:
        market = _make_market(session)
        forecast = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="test-run",
            trigger_type="manual",
            day=date(2026, 8, 5),
            now=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        )

    assert forecast.status == "FAILED"
    assert forecast.fair_value is None
    assert forecast.retries == 2  # exhausted the bounded retry policy (initial attempt + 2 retries)


def test_ok_forecast_is_immutable_for_its_run_slot(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    call_count = {"n": 0}

    def fake_post(url, json=None, timeout=None):
        call_count["n"] += 1
        fair_value = 0.30 if call_count["n"] == 1 else 0.90
        body = {
            "fair_value": fair_value,
            "confidence": 0.5,
            "should_abstain": False,
            "rationale_short": "r",
            "key_uncertainties": ["u"],
            "base_rate_notes": "n",
        }
        return _FakeResponse({"response": json_dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        first = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="run-1",
            trigger_type="manual",
            day=date(2026, 8, 5),
            now=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        )
        assert first.status == "OK"
        assert first.fair_value == pytest.approx(0.30)

        # A second attempt for the exact same run_slot must not overwrite the first result.
        second = generate_blind_forecast(
            session,
            market,
            job=None,
            run_key="run-1-rerun",
            trigger_type="manual",
            day=date(2026, 8, 5),
            now=datetime(2026, 8, 5, 10, 5, tzinfo=timezone.utc),
        )
        assert second.id == first.id
        assert second.fair_value == pytest.approx(0.30)
        assert call_count["n"] == 1  # never re-called the model for an already-OK slot

        rows = list(session.scalars(select(LLMForecast).where(LLMForecast.market_id == market.id)))
        assert len(rows) == 1


def test_twice_daily_slots_append_distinct_rows_same_day(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="ollama", llm_base_url="http://fake-ollama:11434", llm_model="qwen3:8b")
    monkeypatch.setattr("app.ppi.blind_forecast.get_settings", lambda: settings)

    def fake_post(url, json=None, timeout=None):
        body = {
            "fair_value": 0.5,
            "confidence": 0.5,
            "should_abstain": False,
            "rationale_short": "r",
            "key_uncertainties": ["u"],
            "base_rate_notes": "n",
        }
        return _FakeResponse({"response": json_dumps(body)})

    monkeypatch.setattr("requests.post", fake_post)

    with Session.begin() as session:
        market = _make_market(session)
        day = date(2026, 8, 5)
        generate_blind_forecast(
            session, market, job=None, run_key="primary", trigger_type="github-1", day=day,
            now=datetime(2026, 8, 5, 10, 17, tzinfo=timezone.utc),
        )
        generate_blind_forecast(
            session, market, job=None, run_key="backup", trigger_type="github-2", day=day,
            now=datetime(2026, 8, 5, 22, 17, tzinfo=timezone.utc),
        )
        rows = list(session.scalars(select(LLMForecast).where(LLMForecast.market_id == market.id)))

    assert {r.run_slot for r in rows} == {"2026-08-05:primary", "2026-08-05:backup"}
    assert len(rows) == 2


def json_dumps(obj) -> str:
    return json.dumps(obj)


def test_prompt_is_reproducible_across_a_fresh_sqlite_session(tmp_path):
    """Regression test for a real bug found during a live run: SQLite's DateTime(timezone=True)
    columns come back tzinfo-naive on a fresh read (a known SQLite+SQLAlchemy limitation), so an
    in-process (tz-aware) datetime and the same value reloaded from disk (tz-naive) used to
    render different .isoformat() strings for market.end_date and evidence published_at -- making
    the persisted prompt_hash unreproducible from a new session, which breaks auditability."""
    engine = create_engine(f"sqlite:///{tmp_path / 'repro.db'}")
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(engine, expire_on_commit=False)

    with SessionFactory.begin() as session:
        market = Market(
            platform_market_id="1",
            tracking_id="T-1",
            question="Will the test resolve YES?",
            rules="Resolves YES if the test passes.",
            end_date=datetime(2026, 11, 3, tzinfo=timezone.utc),
            enabled=True,
        )
        session.add(market)
        session.flush()
        session.add(
            EvidenceItem(
                market_id=market.id,
                source_type="rss",
                source_name="Example",
                title="Recent poll shows tight race",
                normalized_title="recent poll shows tight race",
                content_hash="hash1",
                content_text="A new poll shows a tight race.",
                relevant=True,
                published_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
            )
        )
        market_id = market.id

    # Same-session (in-memory, tz-aware objects never round-tripped through SQLite).
    with SessionFactory() as same_session:
        market_in_session = same_session.get(Market, market_id)
        packet_a, _ = build_blind_evidence_packet(same_session, market_in_session)
        hash_a = hashlib.sha256(build_prompt(packet_a).encode("utf-8")).hexdigest()

    # Brand-new session: SQLite reloads end_date/published_at as tzinfo-naive.
    with SessionFactory() as fresh_session:
        market_fresh = fresh_session.get(Market, market_id)
        assert market_fresh.end_date.tzinfo is None  # confirms the SQLite quirk this guards against
        packet_b, _ = build_blind_evidence_packet(fresh_session, market_fresh)
        hash_b = hashlib.sha256(build_prompt(packet_b).encode("utf-8")).hexdigest()

    assert hash_a == hash_b
    assert packet_a["end_date"] == "2026-11-03T00:00:00+00:00"
    assert packet_b["end_date"] == "2026-11-03T00:00:00+00:00"


def test_require_live_classifier_excludes_fallback_and_failed_evidence(tmp_path):
    """Regression test for a real bug found during a live canonical run: evidence discovery is
    deduplicated by content hash across runs, so a strict/canonical run could silently reuse a
    deterministic_fallback (or ollama_failed) item's relevance judgment left over from an earlier
    non-strict run, even though relevant.is_(True) alone doesn't distinguish who classified it."""
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = _make_market(session)
        session.add_all(
            [
                EvidenceItem(
                    market_id=market.id,
                    source_type="rss",
                    source_name="Example",
                    title="Live-classified item",
                    normalized_title="live-classified item",
                    content_hash="hash-live",
                    relevant=True,
                    classifier_provider="ollama",
                    published_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
                ),
                EvidenceItem(
                    market_id=market.id,
                    source_type="rss",
                    source_name="Example",
                    title="Fallback-classified item from an earlier non-strict run",
                    normalized_title="fallback-classified item",
                    content_hash="hash-fallback",
                    relevant=True,
                    classifier_provider="deterministic_fallback",
                    published_at=datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc),
                ),
            ]
        )
        session.flush()

        packet_default, ids_default = build_blind_evidence_packet(session, market)
        packet_strict, ids_strict = build_blind_evidence_packet(session, market, require_live_classifier=True)

    assert len(ids_default) == 2  # default behavior is unchanged: both relevant items included
    assert len(ids_strict) == 1  # strict mode excludes the fallback-classified item
    assert packet_strict["evidence"][0]["title"] == "Live-classified item"
    assert all("Fallback-classified" not in e["title"] for e in packet_strict["evidence"])
