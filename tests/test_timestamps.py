from datetime import datetime, timezone
from codetalker.utils.timestamps import normalize_timestamp


def test_normalize_iso_string():
    # ISO with Z
    res = normalize_timestamp("2026-03-08T21:33:09.363Z")
    assert res is not None
    assert "2026-03-08" in res
    assert "21:33:09" in res

    # ISO with offset
    res2 = normalize_timestamp("2026-08-22T13:51:21+01:00")
    assert res2 is not None
    assert "2026-08-22" in res2
    assert "12:51:21" in res2  # UTC


def test_normalize_epoch_seconds():
    # Float seconds (e.g. ChatGPT create_time)
    ts = 1718900000.123456
    res = normalize_timestamp(ts)
    assert res is not None
    assert "2024-06-20" in res


def test_normalize_epoch_milliseconds():
    # Int milliseconds (e.g. Cursor / Copilot createdAt)
    ts_ms = 1718900000000
    res = normalize_timestamp(ts_ms)
    assert res is not None
    assert "2024-06-20" in res


def test_normalize_datetime_object():
    dt = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    res = normalize_timestamp(dt)
    assert res == "2026-05-01T12:00:00+00:00"


def test_normalize_invalid_and_none():
    assert normalize_timestamp(None) is None
    assert normalize_timestamp("") is None
    assert normalize_timestamp("not-a-timestamp") is None
