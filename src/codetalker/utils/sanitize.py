from __future__ import annotations

import re


_PROTOBUF_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_protobuf_text(text: str | None) -> str:
    """Remove non-printable control characters common in protobuf-decoded chat text."""
    if not text:
        return ""
    return _PROTOBUF_CONTROL_RE.sub("", text)
