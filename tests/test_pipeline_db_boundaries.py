"""The canonical pipeline must not hold a DB transaction open across the multi-minute LLM calls.

After this change each market is two short committed transactions -- {sources, evidence,
snapshot} then {forecasts, price joins} -- so the pooled connection is returned to the pool
(kept warm by TCP keepalives) during the external waits instead of a reaped connection losing
the whole market.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import event

from app.db.models import Market
from tests.test_dual_series_pipeline import _FakePolymarketClient, _setup


class _PerMarketFakeClient(_FakePolymarketClient):
    def fetch_market(self, market):
        return {"id": market.platform_market_id, "question": market.question}, "https://fake/m", 200


def test_snapshot_transaction_commits_before_the_forecast_inserts(tmp_path, monkeypatch):
    pipeline_module, Session = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline_module, "TrackedPolymarketClient", _PerMarketFakeClient)

    with Session.begin() as s:
        s.add(Market(platform_market_id="1", tracking_id="T-1", question="Q1?",
                     rules="r", enabled=True, active=True, closed=False))

    engine = Session.kw["bind"]
    log: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _bce(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        s = " ".join(statement.split()).upper()
        if s.startswith("INSERT INTO MARKET_SNAPSHOTS"):
            log.append("INSERT_SNAPSHOT")
        elif s.startswith("INSERT INTO LLM_FORECASTS"):
            log.append("INSERT_FORECAST")

    @event.listens_for(engine, "commit")
    def _commit(conn):  # noqa: ANN001
        log.append("COMMIT")

    result = pipeline_module.run_daily_pipeline(
        "primary", run_date=date(2026, 9, 3), strict_llm_only=True, lock_path=tmp_path / "p.lock"
    )
    assert result["status"] == "OK"

    assert "INSERT_SNAPSHOT" in log and "INSERT_FORECAST" in log and "COMMIT" in log, log
    first_snap = log.index("INSERT_SNAPSHOT")
    first_fc = log.index("INSERT_FORECAST")
    # A COMMIT lands between the snapshot INSERT and the first forecast INSERT: the
    # metadata/snapshot transaction closed before the two generate_blind_forecast calls.
    assert "COMMIT" in log[first_snap:first_fc], log
    assert first_snap < first_fc, log


def test_db_diagnostics_is_sanitized():
    from app.db.database import db_diagnostics

    diag = db_diagnostics()
    assert diag["connection_mode"] in {"sqlite", "session-pooler", "transaction-pooler", "direct-postgres"}
    blob = repr(diag).lower()
    for secret_ish in ("://", "@", "password", "postgres"):
        assert secret_ish not in blob
