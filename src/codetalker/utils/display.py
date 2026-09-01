from __future__ import annotations

DISPLAY_NAME_MAX_LEN = 50


def clip_display_name(title: str | None, max_len: int = DISPLAY_NAME_MAX_LEN) -> tuple[str | None, bool]:
    """Clip a session title for list metadata; returns (title, was_truncated)."""
    if not title:
        return None, False
    text = title.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text, False
    if max_len <= 1:
        return "…", True
    return text[: max_len - 1].rstrip() + "…", True
