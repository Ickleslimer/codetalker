from __future__ import annotations

import os
from urllib.parse import unquote, urlparse


def normalize_working_directory(path: str | None) -> str | None:
    """Normalize workspace paths to plain filesystem paths when possible."""
    if not path:
        return None
    cleaned = path.strip().strip("'\"")
    if cleaned.startswith("file://"):
        parsed = urlparse(cleaned)
        raw = unquote(parsed.path or "")
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        cleaned = raw.replace("/", "\\") if ":" in raw[:3] else raw
    elif ":" in cleaned[:3]:
        cleaned = cleaned.replace("/", "\\")
    return os.path.normpath(cleaned)


def _canonical_working_directory(path: str | None) -> str | None:
    normalized = normalize_working_directory(path)
    if not normalized:
        return None
    return os.path.normcase(os.path.normpath(normalized))


def working_directories_match(
    candidate: str | None,
    query: str | None,
    *,
    allow_prefix: bool = True,
) -> bool:
    """Return True when candidate and query refer to the same workspace path."""
    candidate_key = _canonical_working_directory(candidate)
    query_key = _canonical_working_directory(query)
    if not candidate_key or not query_key:
        return False
    if candidate_key == query_key:
        return True
    if not allow_prefix:
        return False
    sep = os.sep
    return candidate_key.startswith(query_key + sep) or query_key.startswith(
        candidate_key + sep
    )
