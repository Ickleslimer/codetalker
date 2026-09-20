"""Fresh-session detection and continue-token anchoring (codetalker v0.3).

Context loss arrives in two classes, and they need different mechanisms:

LOUD wipes — harness restarts and failed turns — inject visible markers into
the persisted transcript (``<since_your_last_turn>``, ``<failed_turn>``, and
system notices about a session ending mid-response). :func:`detect_session_fresh`
classifies those mechanically from the last N steps; no model judgment.

SILENT wipes — context dropped at a message boundary mid-session — leave no
transcript artifact at all. No external tool can detect them from disk, so
detection stays with the model (the antecedent check). What this module adds
is verifiability: :func:`emit_continue_token` produces a signed anchor and
records it in a local issuance ledger, and :func:`verify_continue_token`
proves — or refutes — the agent's claimed memory against that ledger (with a
transcript fallback for pre-0.3.5 tokens that predate it).

The token is an integrity anchor, not a secret: the signature detects
accidental truncation or editing, and the version constant is bumped when the
payload format changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from codetalker.schema import ActorRole, BlockType, NormalizedStep

# ─── LOUD-wipe detection ─────────────────────────────────────────────────────

# Substrings observed in real persisted transcripts when the harness restarts
# mid-thread or a turn fails. Matched against user- and system-role text only;
# assistant or tool text quoting these strings (e.g. in notes) must NOT trip
# the detector.
RESTART_MARKERS: tuple[str, ...] = (
    "<since_your_last_turn>",
    "<failed_turn>",
    "Freebuff could not complete the previous turn",
    "The session ended before this response completed",
)

DEFAULT_SCAN_WINDOW = 60


def extract_step_text(step: NormalizedStep) -> str:
    """Concatenated TEXT-block content of a step ("" when none)."""
    parts: list[str] = []
    for block in step.blocks:
        if getattr(block, "type", None) is BlockType.TEXT:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts)


@dataclass
class FreshnessReport:
    """Mechanical classification of whether the latest turn started fresh."""

    is_fresh: bool
    signal: str
    marker_step_index: int | None = None
    steps_scanned: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "is_fresh": self.is_fresh,
            "signal": self.signal,
            "marker_step_index": self.marker_step_index,
            "steps_scanned": self.steps_scanned,
        }


def detect_session_fresh(
    steps: Sequence[NormalizedStep],
    scan_window: int = DEFAULT_SCAN_WINDOW,
) -> FreshnessReport:
    """Scan the transcript tail for harness wipe markers.

    Only USER- and SYSTEM-role steps are examined: the harness injects its
    restart/failure notices into those positions. Steps are scanned newest
    last; the report records the newest matching step index.
    """
    tail = list(steps)[-max(int(scan_window), 1) :]
    report = FreshnessReport(is_fresh=False, signal="none", steps_scanned=len(tail))
    for step in tail:
        if step.actor.role not in (ActorRole.USER, ActorRole.SYSTEM):
            continue
        text = extract_step_text(step)
        if not text:
            continue
        for marker in RESTART_MARKERS:
            if marker in text:
                report.is_fresh = True
                report.signal = "restart_or_failure_marker"
                report.marker_step_index = step.step_index
    return report


# ─── SILENT-wipe anchoring (continue token) ──────────────────────────────────

CONTINUE_TOKEN_PREFIX = "codetalker-v3-continue"

# Server-side issuance ledger. Every anchor emit_continue_token() produces is
# recorded here; codetalk_recover_token verifies claims against this ledger
# first (no transcript peeking) and falls back to transcript verification for
# pre-0.3.5 tokens. Override the path (or point it at os.devnull) in tests via
# CODETALKER_TOKEN_LEDGER.
TOKEN_LEDGER_PATH = os.environ.get(
    "CODETALKER_TOKEN_LEDGER",
    os.path.join(os.path.expanduser("~"), ".codetalker", "tokens.jsonl"),
)


def _load_issued_anchors(path: str | None = None) -> list[dict]:
    """Read the issuance ledger; a missing or corrupt file means "no records"."""
    ledger_path = path or TOKEN_LEDGER_PATH
    try:
        with open(ledger_path, "r", encoding="utf-8") as fh:
            records: list[dict] = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue  # one bad line never poisons the whole ledger
                if isinstance(record, dict):
                    records.append(record)
            return records
    except OSError:
        return []


def record_issued_anchor(token_line: str, path: str | None = None) -> bool:
    """Append an issued anchor to the ledger. Returns False when it failed.

    Best-effort by design: if the ledger cannot be written (read-only home,
    disk full) the anchor still verifies through the legacy transcript path,
    so recording must never break recovery itself.
    """
    token = parse_continue_token(token_line)
    if token is None:
        return False
    ledger_path = path or TOKEN_LEDGER_PATH
    record = {
        "session_id": token.session_id,
        "working_directory": token.working_directory,
        "last_user_step_index": token.last_user_step_index,
        "total_steps": token.total_steps,
        "last_user_text": token.last_user_text,
        "continuity_mode": token.continuity_mode,
        "sig": _token_signature(token),
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        if ledger_path == os.devnull:
            return True  # sink mode: claims are refused without touching disk
        parent = os.path.dirname(ledger_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False
CONTINUITY_MODE = "codetalker-v3"
# Version salt for the integrity signature. Bump when the payload fields change
# so stale tokens fail verification instead of half-matching.
_TOKEN_SECRET_V1 = "codetalker-continue-token-v1"

_TOKEN_LINE_RE = re.compile(
    re.escape(CONTINUE_TOKEN_PREFIX) + r"\s*(\{.*\})", re.IGNORECASE | re.DOTALL
)


@dataclass
class ContinueToken:
    session_id: str | None = None
    working_directory: str | None = None
    last_user_step_index: int | None = None
    total_steps: int | None = None
    last_user_text: str = ""
    continuity_mode: str = CONTINUITY_MODE


@dataclass
class AnchorCheck:
    matches: bool
    reason: str


def _token_signature(token: ContinueToken) -> str:
    payload = "|".join(
        str(part)
        for part in (
            _TOKEN_SECRET_V1,
            token.session_id or "",
            token.working_directory or "",
            token.last_user_step_index if token.last_user_step_index is not None else "",
            token.total_steps if token.total_steps is not None else "",
            token.last_user_text,
            token.continuity_mode,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def emit_continue_token(
    *,
    session_id: str | None,
    working_directory: str | None,
    last_user_step_index: int | None,
    total_steps: int | None,
    last_user_text: str,
) -> str:
    """Signed anchor recording where this session's last user turn ended.

    v0.3.5: returned to recovery callers and recorded in the issuance ledger;
    agents no longer append it anywhere. Keeps the final user text short
    (an 80-char anchor prefix, not the content itself).
    """
    anchor = (last_user_text or "").strip().replace("\n", " ")[:80]
    token = ContinueToken(
        session_id=session_id,
        working_directory=working_directory,
        last_user_step_index=last_user_step_index,
        total_steps=total_steps,
        last_user_text=anchor,
    )
    body = {
        "session_id": token.session_id,
        "working_directory": token.working_directory,
        "last_user_step_index": token.last_user_step_index,
        "total_steps": token.total_steps,
        "last_user_text": token.last_user_text,
        "continuity_mode": token.continuity_mode,
        "sig": _token_signature(token),
    }
    line = f"{CONTINUE_TOKEN_PREFIX} {json.dumps(body, ensure_ascii=False, separators=(',', ':'))}"
    # v0.3.5: record every issued anchor server-side so verification never
    # depends on the agent having echoed the line into the transcript.
    record_issued_anchor(line)
    return line


def parse_continue_token(text: str) -> ContinueToken | None:
    """Extract the newest continue-token line from arbitrary turn text.

    Returns None when absent, truncated, edited, or signature-invalid — the
    caller treats any of those exactly like "no token claimed".
    """
    if not text or CONTINUE_TOKEN_PREFIX not in text:
        return None
    match = None
    for match in _TOKEN_LINE_RE.finditer(text):
        pass  # keep the last occurrence
    if match is None:
        return None
    try:
        body = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    token = ContinueToken(
        session_id=body.get("session_id"),
        working_directory=body.get("working_directory"),
        last_user_step_index=body.get("last_user_step_index"),
        total_steps=body.get("total_steps"),
        last_user_text=body.get("last_user_text") or "",
        continuity_mode=body.get("continuity_mode") or CONTINUITY_MODE,
    )
    if body.get("sig") != _token_signature(token):
        return None
    return token


def verify_continue_token(
    token: ContinueToken | None,
    steps: Sequence[NormalizedStep],
    *,
    session_id: str | None = None,
    total_steps: int | None = None,
    ledger_path: str | None = None,
) -> AnchorCheck:
    """Compare a claimed anchor against the issuance ledger, then the transcript.

    v0.3.5, ledger-first: every anchor this server issued is recorded in a
    local JSONL ledger, so a signature-valid claim whose (session_id,
    last_user_step_index, sig) tuple is present there matches WITHOUT reading
    any transcript — the agent's own text is no longer evidence for or against
    its memory. Claims absent from the ledger fall back to the original
    transcript check (append-only structural + anchored-text verification),
    which keeps pre-0.3.5 tokens working. A signature-valid token that is in
    NO ledger and matches NO transcript is refused: v0.3.5+ tokens are always
    recorded at issuance, so absence means the payload was hand-crafted or a
    genuine anchor was edited after issuance — both warrant refusal.
    """
    if token is None:
        return AnchorCheck(matches=False, reason="no token claimed")
    if token.continuity_mode != CONTINUITY_MODE:
        return AnchorCheck(
            matches=False, reason=f"unknown continuity_mode {token.continuity_mode!r}"
        )
    if session_id and token.session_id and token.session_id != session_id:
        return AnchorCheck(
            matches=False, reason="token session_id differs from resolved session"
        )

    # ── v0.3.5 ledger-first check ────────────────────────────────────────────
    # A recorded issuance proves the server itself emitted this exact anchor.
    for record in _load_issued_anchors(ledger_path):
        if (
            record.get("session_id") == token.session_id
            and record.get("last_user_step_index") == token.last_user_step_index
            and record.get("sig") == _token_signature(token)
        ):
            return AnchorCheck(matches=True, reason="anchor matches issuance ledger")
    effective_total = total_steps if total_steps is not None else len(steps)
    if token.total_steps is not None and effective_total < token.total_steps:
        return AnchorCheck(
            matches=False,
            reason=(
                f"transcript has {effective_total} steps, token claims "
                f"{token.total_steps}"
            ),
        )
    if token.last_user_step_index is not None and token.last_user_text:
        upper = token.last_user_step_index
        candidates = [s for s in steps if s.step_index <= upper and s.actor.role == ActorRole.USER]
        for step in reversed(candidates):
            text = extract_step_text(step)
            if token.last_user_text and token.last_user_text in text:
                return AnchorCheck(matches=True, reason="anchor found in transcript")
        return AnchorCheck(
            matches=False,
            reason="anchored user text not found at or before the recorded step",
        )
    # Pre-0.3.5 tokens never saw a ledger, so absent + structurally consistent
    # still matches. Fresh tokens that match nothing were tampered with after
    # issuance — refuse rather than reward the edit.
    return AnchorCheck(
        matches=False,
        reason="anchor matches no issuance ledger entry and no transcript evidence",
    )


def instruction_block(report: FreshnessReport) -> str:
    """Short directive string derived from a freshness report (for tool docs)."""
    if report.is_fresh:
        return (
            "RESTART/FAILURE MARKERS detected in the transcript tail: the "
            "in-harness context for earlier turns is gone. Recover, state one "
            "line of what you recovered, then act."
        )
    return "No restart markers found; treat context as intact unless the antecedent check fails."
