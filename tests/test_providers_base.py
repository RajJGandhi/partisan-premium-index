from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models_quant import DataProviderRun, ProviderCache, ProviderHealth
from app.providers.base import (
    FAILED,
    OK,
    STALE,
    BaseProvider,
    ProviderChain,
    ProviderError,
)


class _Canned(BaseProvider):
    kind = "poll"

    def __init__(self, name, payload, *, fail_times=0, always_fail=False, **kw):
        self.name = name
        self.endpoint_family = f"test:{name}"
        kw.setdefault("backoff_base_seconds", 0)
        super().__init__(**kw)
        self._payload = payload
        self._fail_times = fail_times
        self._always_fail = always_fail
        self.calls = 0

    def _do_fetch(self, **kwargs):
        self.calls += 1
        if self._always_fail or self.calls <= self._fail_times:
            raise ProviderError(f"{self.name}: synthetic failure #{self.calls}")
        return self._payload, f"https://example.test/{self.name}", 200

    def _normalize(self, raw, **kwargs):
        return list(raw or [])


def test_success_writes_cache_and_health(quant_db):
    with quant_db() as s:
        p = _Canned("ok_provider", [{"a": 1}, {"a": 2}])
        res = p.fetch(s)
        assert res.status == OK and res.normalized_payload == [{"a": 1}, {"a": 2}]
        assert res.content_hash and res.retries == 0
        s.commit()
        assert s.query(ProviderCache).filter_by(provider_name="ok_provider", ok=True).count() == 1
        h = s.query(ProviderHealth).filter_by(provider_name="ok_provider").one()
        assert h.status == "HEALTHY" and h.consecutive_failures == 0


def test_retries_then_succeeds(quant_db):
    with quant_db() as s:
        p = _Canned("flaky", [{"x": 1}], fail_times=2, max_retries=3)
        res = p.fetch(s)
        assert res.status == OK
        assert p.calls == 3
        assert res.retries == 2


def test_total_failure_is_FAILED_not_zero(quant_db):
    with quant_db() as s:
        p = _Canned("down", [{"x": 1}], always_fail=True, max_retries=2)
        res = p.fetch(s)
        assert res.status == FAILED
        assert res.normalized_payload is None  # missing, NOT an empty/zero list masquerading as data
        assert not res.usable
        s.commit()
        h = s.query(ProviderHealth).filter_by(provider_name="down").one()
        assert h.status in {"DEGRADED", "DOWN"} and h.is_stale and h.consecutive_failures >= 1


def test_fresh_cache_hit_skips_the_network(quant_db):
    with quant_db() as s:
        p = _Canned("cached", [{"n": 1}])
        p.fetch(s)
        s.commit()
        p2 = _Canned("cached", [{"n": 999}])  # different payload; must NOT be used
        res = p2.fetch(s)
        assert res.from_cache is True
        assert res.normalized_payload == [{"n": 1}]
        assert p2.calls == 0


def test_last_known_good_serves_STALE_after_failure(quant_db):
    with quant_db() as s:
        _Canned("lkg", [{"good": 1}]).fetch(s)
        s.commit()
        # age the cache row past TTL so it is not a *fresh* hit, only last-known-good
        for row in s.query(ProviderCache).filter_by(provider_name="lkg"):
            row.fetched_at = datetime.now(timezone.utc) - timedelta(days=30)
        s.commit()
        res = _Canned("lkg", [{"x": 1}], always_fail=True, max_retries=2).fetch(s)
        assert res.status == STALE
        assert res.from_last_known_good is True
        assert res.normalized_payload == [{"good": 1}]
        s.commit()
        h = s.query(ProviderHealth).filter_by(provider_name="lkg").one()
        assert h.is_stale and h.status == "DEGRADED"


def test_disabled_provider_yields_empty_not_error(quant_db):
    class _Disabled(_Canned):
        def enabled(self):
            return False

    with quant_db() as s:
        res = _Disabled("off", []).fetch(s)
        assert res.status == "EMPTY"
        assert res.error and "not enabled" in res.error


def test_chain_falls_back_and_records_one_run(quant_db):
    with quant_db() as s:
        chain = ProviderChain(
            "poll",
            [
                _Canned("primary", [], always_fail=True, max_retries=1),
                _Canned("secondary", [{"from": "secondary"}]),
            ],
        )
        cr = chain.run(s, target_ref="polls:2026")
        assert cr.usable
        assert cr.provider_used == "secondary"
        assert cr.provider_requested == "primary"
        assert "primary=failed" in cr.fallback_reason
        s.commit()
        runs = s.query(DataProviderRun).filter_by(provider_kind="poll").all()
        assert len(runs) == 1
        assert runs[0].provider_requested == "primary" and runs[0].provider_used == "secondary"
        assert runs[0].status == OK


def test_chain_all_fail_records_failed_run(quant_db):
    with quant_db() as s:
        chain = ProviderChain(
            "poll",
            [
                _Canned("a", [], always_fail=True, max_retries=1),
                _Canned("b", [], always_fail=True, max_retries=1),
            ],
        )
        cr = chain.run(s, target_ref="x")
        assert not cr.usable and cr.provider_used is None
        s.commit()
        run = s.query(DataProviderRun).filter_by(provider_kind="poll").one()
        assert run.status == FAILED and run.sanitized_error
