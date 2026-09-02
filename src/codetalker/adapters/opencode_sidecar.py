from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
import urllib.error
import urllib.request
from typing import Any

from codetalker.schema import (
    Actor,
    ActorRole,
    ContentBlock,
    NormalizedSession,
    NormalizedStep,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from codetalker.utils.display import clip_display_name
from codetalker.utils.paths import normalize_working_directory
from codetalker.utils.timestamps import normalize_timestamp

logger = logging.getLogger("codetalker.adapters.opencode_sidecar")

_SKIP_PART_TYPES = frozenset({"step-start", "step-finish"})
_SIDECAR_URL_RE = re.compile(
    r"server ready\s*\{\s*url:\s*['\"](https?://[^'\"]+)['\"]"
)


def default_opencode_db_path() -> str | None:
    """Return the default OpenCode sidecar storage database, if present."""
    candidates = [
        os.path.join(os.path.expanduser("~"), ".local", "share", "opencode", "opencode.db"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "opencode", "opencode.db"),
        os.path.join(os.environ.get("XDG_DATA_HOME", ""), "opencode", "opencode.db"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def discover_sidecar_server_url() -> str | None:
    """Resolve a live OpenCode sidecar URL from env or recent desktop logs."""
    env_url = os.environ.get("OPENCODE_SERVER_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")

    desktop_logs = os.path.join(
        os.path.expanduser("~/AppData/Roaming/ai.opencode.desktop/logs"),
        "*",
        "main.log",
    )
    log_paths = sorted(glob.glob(desktop_logs), reverse=True)
    for log_path in log_paths[:5]:
        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            matches = _SIDECAR_URL_RE.findall(content)
            if matches:
                return matches[-1].rstrip("/")
        except OSError as e:
            logger.debug("Failed to read OpenCode desktop log %s: %s", log_path, e)
    return None


def _http_get_json(url: str, timeout: float = 3.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_sidecar_messages(session_id: str, base_url: str | None = None) -> list[dict[str, Any]] | None:
    """Fetch session messages from a running OpenCode sidecar HTTP server."""
    base = (base_url or discover_sidecar_server_url() or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/session/{session_id}/message"
    try:
        payload = _http_get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        logger.debug("OpenCode sidecar fetch failed for %s: %s", session_id, e)
        return None
    if not isinstance(payload, list):
        return None
    return payload


def _parse_model_field(raw: Any) -> str | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, dict):
        return raw.get("modelID") or raw.get("id") or raw.get("providerID")
    return None


def _message_timestamp(info: dict[str, Any], fallback: Any = None) -> str | None:
    time_val = info.get("time")
    if isinstance(time_val, dict):
        for key in ("created", "start", "updated"):
            ts = normalize_timestamp(time_val.get(key))
            if ts:
                return ts
    ts = normalize_timestamp(time_val)
    if ts:
        return ts
    return normalize_timestamp(fallback)


def _part_to_blocks(part: dict[str, Any]) -> list[ContentBlock]:
    ptype = part.get("type")
    if ptype in _SKIP_PART_TYPES:
        return []

    if ptype == "text":
        text = part.get("text") or ""
        return [TextBlock(text=text)] if text else []

    if ptype in ("reasoning", "thinking"):
        text = part.get("text") or ""
        return [ThinkingBlock(text=text)] if text else []

    if ptype == "tool":
        state = part.get("state") or {}
        tool_name = str(part.get("tool") or "tool")
        call_id = part.get("callID")
        blocks: list[ContentBlock] = []
        tool_input = state.get("input")
        if isinstance(tool_input, dict) and tool_input:
            blocks.append(
                ToolCallBlock(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    tool_args=tool_input,
                )
            )
        output = state.get("output")
        metadata = state.get("metadata") or {}
        if output is None and metadata.get("output") is not None:
            output = metadata.get("output")
        if output is not None:
            status = str(state.get("status") or "").lower()
            is_error = status in {"error", "failed", "failure"}
            blocks.append(
                ToolResultBlock(
                    tool_call_id=call_id,
                    tool_name=tool_name,
                    content=str(output),
                    is_error=is_error,
                )
            )
        return blocks

    text = part.get("text")
    if isinstance(text, str) and text.strip():
        return [TextBlock(text=text)]
    return []


def _messages_to_steps(
    messages: list[dict[str, Any]],
    *,
    default_timestamp: str | None = None,
    include_raw_data: bool = False,
) -> list[NormalizedStep]:
    steps: list[NormalizedStep] = []
    step_idx = 0

    for entry in messages:
        info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
        parts = entry.get("parts")
        if parts is None and isinstance(entry.get("part"), list):
            parts = entry.get("part")
        if not isinstance(info, dict):
            continue

        role_str = info.get("role") or "assistant"
        role = (
            ActorRole.USER
            if role_str == "user"
            else ActorRole.ASSISTANT
            if role_str == "assistant"
            else ActorRole.SYSTEM
        )
        ts = _message_timestamp(info, default_timestamp)
        model = _parse_model_field(info.get("model"))

        if isinstance(parts, list) and parts:
            for part in parts:
                if not isinstance(part, dict):
                    continue
                blocks = _part_to_blocks(part)
                if not blocks:
                    continue
                steps.append(
                    NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or default_timestamp,
                        actor=Actor(role=role, model=model if role == ActorRole.ASSISTANT else None),
                        blocks=blocks,
                        **({"raw_data": part} if include_raw_data else {}),
                        harness_step_type=f"opencode_{part.get('type', role_str)}",
                    )
                )
                step_idx += 1
        else:
            summary = info.get("summary")
            if isinstance(summary, str) and summary.strip():
                steps.append(
                    NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or default_timestamp,
                        actor=Actor(role=role, model=model if role == ActorRole.ASSISTANT else None),
                        blocks=[TextBlock(text=summary)],
                        **({"raw_data": info} if include_raw_data else {}),
                        harness_step_type=f"opencode_{role_str}",
                    )
                )
                step_idx += 1

    return steps


def discover_sessions_from_db(db_path: str) -> list[NormalizedSession]:
    sessions: list[NormalizedSession] = []
    if not os.path.isfile(db_path):
        return sessions

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                s.id,
                s.title,
                s.directory,
                s.time_created,
                s.time_updated,
                s.model,
                COUNT(DISTINCT m.id) AS message_count,
                SUM(CASE WHEN json_extract(m.data, '$.role') = 'user' THEN 1 ELSE 0 END) AS user_turns,
                SUM(CASE WHEN json_extract(m.data, '$.role') = 'assistant' THEN 1 ELSE 0 END) AS assistant_turns
            FROM session s
            LEFT JOIN message m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.time_updated DESC
            """
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        logger.debug("Failed to discover OpenCode sidecar sessions from %s: %s", db_path, e)
        return sessions

    for row in rows:
        sid, title, directory, created, updated, model_raw, msg_count, user_turns, assistant_turns = row
        started_at = normalize_timestamp(created)
        last_activity = normalize_timestamp(updated) or started_at
        display_name, truncated = clip_display_name(
            (title or "").strip() or f"OpenCode Session {sid[:8]}"
        )
        sessions.append(
            NormalizedSession(
                session_id=sid,
                harness="opencode",
                display_name=display_name,
                conversation_id=sid,
                branch_root_step_id=None,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                working_directory=normalize_working_directory(directory),
                model=_parse_model_field(model_raw) or "OpenCode Agent",
                step_count=int(msg_count or 0),
                user_turn_count=int(user_turns or 0),
                assistant_turn_count=int(assistant_turns or 0),
                source_path=db_path,
                source_format="opencode_sidecar",
                has_dag=False,
                display_name_truncated=truncated,
            )
        )
    return sessions


def _load_db_messages(db_path: str, session_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT m.id, m.time_created, m.data, p.id, p.time_created, p.data
        FROM message m
        LEFT JOIN part p ON p.message_id = m.id
        WHERE m.session_id = ?
        ORDER BY m.time_created ASC, p.time_created ASC
        """,
        (session_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    messages: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for msg_id, msg_created, msg_data, part_id, _part_created, part_data in rows:
        if current is None or current.get("_msg_id") != msg_id:
            if current is not None:
                messages.append({"info": current["info"], "parts": current["parts"]})
            info = json.loads(msg_data) if msg_data else {}
            if not info.get("time"):
                info["time"] = msg_created
            current = {"_msg_id": msg_id, "info": info, "parts": []}
        if part_id and part_data:
            part = json.loads(part_data)
            current["parts"].append(part)
    if current is not None:
        messages.append({"info": current["info"], "parts": current["parts"]})
    return messages


def load_steps_from_sidecar(
    session: NormalizedSession,
    *,
    include_raw_data: bool = False,
) -> list[NormalizedStep]:
    """Load full OpenCode sidecar transcript steps for a session."""
    db_path = session.source_path if session.source_format == "opencode_sidecar" else None
    if not db_path or not os.path.isfile(db_path):
        db_path = default_opencode_db_path()

    messages: list[dict[str, Any]] | None = None
    if db_path and os.path.isfile(db_path):
        try:
            messages = _load_db_messages(db_path, session.session_id)
        except Exception as e:
            logger.debug(
                "OpenCode DB load failed for %s (%s): %s",
                session.session_id,
                db_path,
                e,
            )

    if not messages:
        messages = fetch_sidecar_messages(session.session_id)

    if not messages:
        return []

    return _messages_to_steps(
        messages,
        default_timestamp=session.last_activity,
        include_raw_data=include_raw_data,
    )
