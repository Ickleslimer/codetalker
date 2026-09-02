from __future__ import annotations

import json
import re


_SESSION_NOT_FOUND_RE = re.compile(
    r"session\s+['\"].+['\"]\s+not\s+found|not\s+found.*session",
    re.IGNORECASE,
)


def content_indicates_tool_error(content: str) -> bool:
    """Heuristic: MCP/tool failures often appear as plain text in tool results."""
    if not content or not content.strip():
        return False

    lower = content.lower()
    if _SESSION_NOT_FOUND_RE.search(content):
        return True
    if "mcp error" in lower or "tool error" in lower:
        return True
    if "iserror" in lower and "true" in lower:
        return True
    if lower.startswith("error:") or lower.startswith("error "):
        return True

    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except Exception:
            return False
        if isinstance(parsed, dict):
            if parsed.get("isError") is True or parsed.get("is_error") is True:
                return True
            if parsed.get("error") and parsed.get("error") not in (None, "", False):
                return True
            if isinstance(parsed.get("status"), str) and parsed["status"].upper() in (
                "ERROR",
                "FAILED",
                "FAILURE",
            ):
                return True
    return False
