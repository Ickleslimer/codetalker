from __future__ import annotations

from urllib.parse import unquote, urlparse


def normalize_working_directory(path: str | None) -> str | None:
    """Normalize workspace paths to plain filesystem paths when possible."""
    if not path:
        return None
    cleaned = path.strip()
    if cleaned.startswith("file://"):
        parsed = urlparse(cleaned)
        raw = unquote(parsed.path or "")
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return raw.replace("/", "\\") if ":" in raw[:3] else raw
    return cleaned.replace("/", "\\") if ":" in cleaned[:3] else cleaned
