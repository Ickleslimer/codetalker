from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def normalize_timestamp(value: Any) -> str | None:
    """Normalize any timestamp format into an ISO 8601 UTC string.

    Supports:
    - ISO 8601 strings (e.g. '2025-02-22T12:34:56.789Z', with or without offset)
    - Unix epoch seconds (int or float, e.g. 1718900000.123456)
    - Unix epoch milliseconds (int or float > 1e11, e.g. 1718900000000)
    - datetime objects
    - None / invalid / unparseable -> returns None
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    if isinstance(value, (int, float)):
        # Heuristic: timestamps > 1e11 are in milliseconds (e.g. year 1973+ in ms is > 1e11)
        # Epoch seconds for recent times are ~1.7e9
        if value > 1e11:
            seconds = value / 1000.0
        else:
            seconds = float(value)
        try:
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    if isinstance(value, str):
        val_str = value.strip()
        if not val_str:
            return None

        # Try numeric string (seconds or ms)
        try:
            num = float(val_str)
            return normalize_timestamp(num)
        except ValueError:
            pass

        # Try parsing ISO 8601
        try:
            # Replace trailing 'Z' with '+00:00' for fromisoformat compatibility in Python <= 3.10
            # in 3.11+ fromisoformat handles 'Z' directly
            dt = datetime.fromisoformat(val_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

        # Common format fallbacks: 'YYYY-MM-DD HH:MM:SS'
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(val_str, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue

    return None


def compare_timestamps(a: Any, b: Any) -> int | None:
    """Compare two timestamp values after normalization.

    Returns -1 if a < b, 0 if equal, 1 if a > b, or None if either is unparseable.
    """
    na = normalize_timestamp(a)
    nb = normalize_timestamp(b)
    if na is None or nb is None:
        return None
    if na < nb:
        return -1
    if na > nb:
        return 1
    return 0


def timestamp_gte(value: Any, threshold: str) -> bool:
    """True if value is at or after threshold, or if either side is unparseable (include step)."""
    cmp = compare_timestamps(value, threshold)
    if cmp is None:
        return True
    return cmp >= 0


def timestamp_lte(value: Any, threshold: str) -> bool:
    """True if value is at or before threshold, or if either side is unparseable (include step)."""
    cmp = compare_timestamps(value, threshold)
    if cmp is None:
        return True
    return cmp <= 0
