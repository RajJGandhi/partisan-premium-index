from datetime import datetime, timezone

from app.ppi.pipeline import _upstream_dt


def test_upstream_millisecond_timestamp_is_parsed():
    now = datetime.now(timezone.utc)
    parsed = _upstream_dt(int(now.timestamp() * 1000))
    assert abs((parsed - now).total_seconds()) < 2
