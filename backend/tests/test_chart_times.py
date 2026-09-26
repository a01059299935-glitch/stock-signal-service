from datetime import datetime, timezone

from app.providers import to_chart_time


def test_daily_session_timestamp_is_date_not_market_open_time():
    assert to_chart_time(1790343000, "1D") == "2026-09-25"


def test_intraday_timestamp_remains_unix_seconds():
    assert to_chart_time(1790343000, "1m") == 1790343000


def test_session_date_uses_exchange_timezone_across_dst():
    # 2026-11-02 00:30 UTC is still the prior US exchange date (EST).
    stamp = int(datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc).timestamp())
    assert to_chart_time(stamp, "1D") == "2026-11-01"
