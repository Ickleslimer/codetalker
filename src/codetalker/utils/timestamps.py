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
