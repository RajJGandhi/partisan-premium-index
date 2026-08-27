from __future__ import annotations

from app.blind.providers import BlindForecastProvider, BlindLLMCall, DeterministicBlindProvider
from app.blind.runner import (
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    run_blind_forecasts,
)
from app.db.models_quant import BlindBenchmarkForecast

CONTRACT = "Will the Democratic candidate win the TX senate general election in 2026?"


class _Fake(BlindForecastProvider):
    def __init__(self, provider_name, *, text=None, raises=None, model="fake-1"):
        self.provider_name = provider_name
        self.model_name = model
        self._text = text
        self._raises = raises
        self.calls = 0

    def enabled(self):
        return True

    def generate(self, *, system, user):
        self.calls += 1
        if self._raises:
            raise self._raises
        return BlindLLMCall(raw_text=self._text, model_name=self.model_name, model_version=self.model_name,
                            prompt_tokens=100, completion_tokens=50, total_tokens=150)


_GOOD = '{"probability": 0.66, "should_abstain": false, "rationale": "lean D + incumbent", "uncertainty_drivers": ["turnout"]}'


def _run(session, bundle, providers, **kw):
    return run_blind_forecasts(
        session, race_id="tx-sen-2026", run_key="quant-shadow:2026-08-27:primary",
        evidence_bundle=bundle, contract_question=CONTRACT, providers=providers, **kw,
    )


def test_persists_one_row_per_provider(quant_db, blind_bundle):
    with quant_db() as s:
        summ = _run(s, blind_bundle, [_Fake("openai", text=_GOOD), _Fake("anthropic", text=_GOOD)])
        s.commit()
        assert len(summ.rows) == 2
        assert all(r.status == STATUS_OK and r.probability == 0.66 for r in summ.rows)
        assert s.query(BlindBenchmarkForecast).count() == 2
        assert summ.total_tokens == 300


def test_disabled_provider_records_skipped_no_value(quant_db, blind_bundle):
    class _Off(_Fake):
        def enabled(self):
            return False

    with quant_db() as s:
        summ = _run(s, blind_bundle, [_Off("openai", text=_GOOD)])
        s.commit()
        row = summ.rows[0]
        assert row.status == STATUS_SKIPPED and row.probability is None
        assert "not enabled" in row.error_message


def test_bad_json_after_retries_is_failed_not_fabricated(quant_db, blind_bundle):
    p = _Fake("openai", text="definitely not json")
    with quant_db() as s:
        summ = _run(s, blind_bundle, [p], max_retries=1)
        s.commit()
        assert summ.rows[0].status == STATUS_FAILED
        assert summ.rows[0].probability is None
        assert p.calls == 2  # 1 + 1 retry


def test_provider_exception_is_failed_not_crash(quant_db, blind_bundle):
    p = _Fake("anthropic", raises=RuntimeError("503 upstream"))
    with quant_db() as s:
        summ = _run(s, blind_bundle, [p], max_retries=0)
        s.commit()
        assert summ.rows[0].status == STATUS_FAILED
        assert "503 upstream" in summ.rows[0].error_message


def test_cost_control_reuses_ok_row_with_same_evidence_hash(quant_db, blind_bundle):
    session_factory = quant_db
    p1 = _Fake("openai", text=_GOOD)
    with session_factory() as s:
        _run(s, blind_bundle, [p1])
        s.commit()
    p2 = _Fake("openai", text=_GOOD)
    with session_factory() as s:
        summ = _run(s, blind_bundle, [p2])
        s.commit()
        assert summ.reused == 1
        assert p2.calls == 0  # no second API call for the same evidence bundle
        assert s.query(BlindBenchmarkForecast).count() == 1


def test_changed_model_appends_new_revision(quant_db, blind_bundle):
    session_factory = quant_db
    with session_factory() as s:
        _run(s, blind_bundle, [_Fake("openai", text=_GOOD, model="fake-1")])
        s.commit()
    with session_factory() as s:
        summ = _run(s, blind_bundle, [_Fake("openai", text=_GOOD, model="fake-2")])
        s.commit()
        assert summ.reused == 0
        rows = s.query(BlindBenchmarkForecast).order_by(BlindBenchmarkForecast.revision).all()
        assert [r.revision for r in rows] == [0, 1]
        assert rows[1].correction_of_id == rows[0].id
        assert rows[0].model_name == "fake-1" and rows[1].model_name == "fake-2"
        # original untouched
        assert rows[0].probability == 0.66


def test_abstain_flag_maps_to_abstained_status(quant_db, blind_bundle):
    txt = '{"probability": 0.5, "should_abstain": true, "rationale": "thin", "uncertainty_drivers": []}'
    with quant_db() as s:
        summ = _run(s, blind_bundle, [_Fake("openai", text=txt)])
        s.commit()
        assert summ.rows[0].status == "ABSTAINED"
        assert summ.rows[0].probability == 0.5  # value retained, but ensemble treats it as missing


def test_stub_rows_flagged_stub(quant_db, blind_bundle):
    with quant_db() as s:
        summ = _run(s, blind_bundle, [DeterministicBlindProvider(bundle=blind_bundle, standing_in_for="openai")])
        s.commit()
        assert summ.rows[0].publication_status == "STUB"
        assert summ.rows[0].status == STATUS_OK


def test_runner_signature_has_no_market_parameter():
    import inspect

    params = set(inspect.signature(run_blind_forecasts).parameters)
    for banned in ("market", "price", "quant_probability", "polymarket", "bid", "ask", "spread", "ensemble"):
        assert not any(banned in p for p in params), f"blind runner exposes {banned!r}"
