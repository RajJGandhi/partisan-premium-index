from __future__ import annotations

from datetime import date

import pytest

from app.quant.config import QUANT_V1
from app.quant.national_environment import compute_national_environment
from app.quant.types import GenericBallotPoll


def _gb(dem, rep, end=date(2026, 8, 20), pollster="GB", **kw):
    return GenericBallotPoll(pollster=pollster, end_date=end, dem_pct=dem, rep_pct=rep, **kw)


def test_generic_ballot_margin_is_dem_minus_rep():
    val, detail = compute_national_environment([_gb(47, 43)], date(2026, 8, 27), cfg=QUANT_V1)
    assert val == pytest.approx(4.0)
    assert detail["source"] == "generic_ballot_polls"


def test_multiple_polls_are_weight_averaged():
    polls = [
        _gb(48, 44, end=date(2026, 8, 25), pollster="Alpha"),
        _gb(46, 46, end=date(2026, 8, 25), pollster="Beta"),
    ]
    val, _ = compute_national_environment(polls, date(2026, 8, 27), cfg=QUANT_V1)
    assert val == pytest.approx(2.0)  # equal weights -> mean of +4 and 0


def test_override_used_only_when_no_polls():
    val, detail = compute_national_environment([], date(2026, 8, 27), override=3.5, cfg=QUANT_V1)
    assert val == 3.5
    assert detail["source"] == "provider_override"
    # polls present -> override ignored
    val2, detail2 = compute_national_environment([_gb(50, 40)], date(2026, 8, 27), override=3.5, cfg=QUANT_V1)
    assert val2 == pytest.approx(10.0)
    assert detail2["source"] == "generic_ballot_polls"


def test_absent_and_no_override_returns_none():
    val, detail = compute_national_environment([], date(2026, 8, 27), cfg=QUANT_V1)
    assert val is None
    assert detail["source"] is None
