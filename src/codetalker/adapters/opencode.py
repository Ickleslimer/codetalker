from __future__ import annotations

import base64
import glob
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter
from codetalker.adapters.opencode_sidecar import (
    default_opencode_db_path,
    discover_sessions_from_db,
    load_steps_from_sidecar,
)
from codetalker.schema import (
    Actor,
    ActorRole,
    BlockType,
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

logger = logging.getLogger("codetalker.adapters.opencode")

DESKTOP_COVERAGE_WARNING = (
    "OpenCode Desktop stores client-side prompt drafts only; "
    "assistant replies and tool executions are not persisted in drafts.sqlite. "
    "Session titles come from opencode.window.*.dat when drafts are empty. "
    "Full transcripts are available from ~/.local/share/opencode/opencode.db "
    "or the live sidecar API when OpenCode Desktop is running."
)


def _decode_b64_path(b64_str: str) -> str | None:
    try:
        padded = b64_str + "=" * ((4 - len(b64_str) % 4) % 4)
        return base64.b64decode(padded).decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug(f"Failed to base64 decode workspace path: {e}")
        return None


class OpenCodeAdapter(BaseAdapter):
    """Adapter for OpenCode Desktop and OpenCode CLI agent sessions."""

    harness_name: str = "opencode"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file():
                if p.name == "opencode.db":
                    sessions.extend(discover_sessions_from_db(str(p)))
                elif p.name.endswith(".sqlite") or p.name.endswith(".db"):
                    sessions.extend(self._discover_from_sqlite(str(p)))
                elif p.name.endswith(".jsonl") or p.name.endswith(".json"):
                    sess = self._inspect_jsonl_file(str(p))
                    if sess:
                        sessions.append(sess)
                if p.name == "drafts.sqlite":
                    sidecar_db = default_opencode_db_path()
                    if sidecar_db:
                        sessions.extend(discover_sessions_from_db(sidecar_db))
                return self._dedupe_sessions(sessions)
            elif p.is_dir():
                for sf in p.glob("**/drafts.sqlite"):
                    sessions.extend(self._discover_from_sqlite(str(sf)))
                for jf in p.glob("**/*.jsonl"):
                    sess = self._inspect_jsonl_file(str(jf))
                    if sess:
                        sessions.append(sess)
                return sessions

        desktop_db = os.path.expanduser("~/AppData/Roaming/ai.opencode.desktop/drafts.sqlite")
        if os.path.isfile(desktop_db):
            sessions.extend(self._discover_from_sqlite(desktop_db))

        sidecar_db = default_opencode_db_path()
        if sidecar_db:
            sessions.extend(discover_sessions_from_db(sidecar_db))

        for base in (
            "~/.opencode/sessions",
            "~/.local/share/opencode/sessions",
            "~/.config/opencode/sessions",
        ):
            exp_base = os.path.expanduser(base)
            if os.path.isdir(exp_base):
                for jf in glob.glob(os.path.join(exp_base, "*.jsonl")):
                    sess = self._inspect_jsonl_file(jf)
                    if sess:
                        sessions.append(sess)

        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return self._dedupe_sessions(sessions)

    @staticmethod
    def _source_priority(source_format: str) -> int:
        return {
            "opencode_sidecar": 0,
            "jsonl": 1,
            "opencode_desktop_draft": 2,
            "sqlite": 3,
        }.get(source_format, 4)

    def _dedupe_sessions(self, sessions: list[NormalizedSession]) -> list[NormalizedSession]:
        """Prefer sidecar DB transcripts over desktop prompt drafts for the same session_id."""
        best: dict[str, NormalizedSession] = {}
        for sess in sessions:
            existing = best.get(sess.session_id)
            if existing is None:
                best[sess.session_id] = sess
                continue
            if self._source_priority(sess.source_format) < self._source_priority(
                existing.source_format
            ):
                best[sess.session_id] = sess
        deduped = list(best.values())
        deduped.sort(key=lambda s: s.last_activity or "", reverse=True)
        return deduped

    def _discover_window_session_metadata(
        self, desktop_dir: str
    ) -> dict[str, dict[str, str]]:
        """Load session title/directory from opencode.window.*.dat tabs.info."""
        metadata: dict[str, dict[str, str]] = {}
        if not os.path.isdir(desktop_dir):
            return metadata

        pattern = os.path.join(desktop_dir, "opencode.window.*.dat")
        for path in glob.glob(pattern):
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                tabs_info_raw = data.get("tabs.info")
                if not tabs_info_raw:
                    continue
                tabs_info = (
                    json.loads(tabs_info_raw)
                    if isinstance(tabs_info_raw, str)
                    else tabs_info_raw
                )
                for tab_key, info in tabs_info.items():
                    if "/session/" not in tab_key:
                        continue
                    sid = tab_key.rsplit("/session/", 1)[-1]
                    if not sid:
                        continue
                    metadata[sid] = {
                        "title": str(info.get("title") or "").strip(),
                        "directory": str(info.get("directory") or "").strip(),
                    }
            except Exception as e:
                logger.debug("Failed to parse OpenCode window file %s: %s", path, e)
        return metadata

    def _discover_from_sqlite(self, db_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        if not os.path.isfile(db_path):
            return sessions

        try:
            mtime = os.path.getmtime(db_path)
            last_activity = normalize_timestamp(mtime)
            desktop_dir = os.path.dirname(db_path)
            window_meta = self._discover_window_session_metadata(desktop_dir)

            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='document'")
            if not cursor.fetchone():
                conn.close()
                return sessions

            cursor.execute("SELECT key, value FROM document")
            rows = cursor.fetchall()

            prompt_history_entries: list[dict[str, Any]] = []
            for key, val_str in rows:
                if key == "opencode.global.dat:prompt-history":
                    try:
                        ph_data = json.loads(val_str)
                        prompt_history_entries = ph_data.get("entries", [])
                    except Exception:
                        pass

            for key, val_str in rows:
                if ":session:" not in key:
                    continue
                parts = key.split(":")
                ses_idx = parts.index("session") if "session" in parts else -1
                sid = parts[ses_idx + 1] if ses_idx != -1 and ses_idx + 1 < len(parts) else key

                ws_path: str | None = None
                if "opencode.workspace." in key:
                    ws_b64 = key.split("opencode.workspace.")[1].split(".")[0]
                    ws_path = _decode_b64_path(ws_b64)

                model_name: str | None = None
                prompt_preview: str | None = None
                session_prompt_count = 0
                try:
                    v_json = json.loads(val_str)
                    model_info = v_json.get("model") or {}
                    model_name = model_info.get("modelID") or model_info.get("providerID")
                    prompts = v_json.get("prompt", [])
                    if prompts and isinstance(prompts, list):
                        session_prompt_count = len(prompts)
                        prompt_preview = prompts[0].get("content")
                except Exception:
                    pass

                win = window_meta.get(sid, {})
                if win.get("directory") and not ws_path:
                    ws_path = win["directory"]
                if win.get("title") and not (prompt_preview and prompt_preview.strip()):
                    prompt_preview = win["title"]

                display_name, truncated = clip_display_name(
                    prompt_preview.strip() if prompt_preview else f"OpenCode Session {sid[:8]}"
                )
                user_turns = max(session_prompt_count, 1 if prompt_preview else 0)

                sessions.append(
                    NormalizedSession(
                        session_id=sid,
                        harness="opencode",
                        display_name=display_name,
                        conversation_id=sid,
                        branch_root_step_id=None,
                        branch_label="Main Thread",
                        started_at=last_activity,
                        last_activity=last_activity,
                        working_directory=normalize_working_directory(ws_path),
                        model=model_name or "OpenCode Agent",
                        step_count=user_turns,
                        user_turn_count=user_turns,
                        assistant_turn_count=0,
                        source_path=db_path,
                        source_format="opencode_desktop_draft",
                        has_dag=False,
                        coverage_warning=DESKTOP_COVERAGE_WARNING,
                        display_name_truncated=truncated,
                    )
                )

            conn.close()
        except Exception:
            pass

        return sessions

    def _inspect_jsonl_file(self, file_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(file_path)
            last_activity = normalize_timestamp(mtime)

            fname = os.path.basename(file_path)
            session_id = fname.replace(".jsonl", "").replace(".json", "")

            display_name: str | None = None
            started_at: str | None = None
            total_steps = 0
            user_turns = 0
            assistant_turns = 0
            model: str | None = None

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total_steps += 1
                    try:
                        data = json.loads(line)
                        ts = normalize_timestamp(data.get("timestamp") or data.get("created_at"))
                        if not started_at and ts:
                            started_at = ts
                        if ts:
                            last_activity = ts

                        role = data.get("role") or data.get("type")
                        if role == "user":
                            user_turns += 1
                            if not display_name:
                                content = data.get("content") or data.get("text")
                                if isinstance(content, str):
                                    display_name, _ = clip_display_name(
                                        content.strip().replace("\n", " ")
                                    )
                        elif role == "assistant":
                            assistant_turns += 1
                            if not model and data.get("model"):
                                model = data.get("model")
                    except Exception:
                        continue

            if not started_at:
                started_at = last_activity

            if not display_name:
                display_name = f"OpenCode Session {session_id[:8]}"

            return NormalizedSession(
                session_id=session_id,
                harness="opencode",
                display_name=display_name,
                conversation_id=session_id,
                branch_root_step_id=None,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                model=model or "OpenCode Agent",
                step_count=total_steps,
                user_turn_count=user_turns,
                assistant_turn_count=assistant_turns,
                source_path=file_path,
                source_format="jsonl",
                has_dag=False,
            )
        except Exception:
            return None

    def load_steps(
        self,
        session: NormalizedSession,
        since: str | None = None,
        until: str | None = None,
        since_last_user_input: bool = False,
        include_step_types: list[BlockType] | None = None,
        include_actor_roles: list[ActorRole] | None = None,
        exclude_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = False,
        max_step_chars: int | None = None,
        offset: int = 0,
        from_end: bool = False,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        if session.source_format == "opencode_sidecar":
            raw_steps = load_steps_from_sidecar(session, include_raw_data=include_raw_data)
        elif session.source_format == "opencode_desktop_draft":
            raw_steps = load_steps_from_sidecar(session, include_raw_data=include_raw_data)
            if not raw_steps:
                raw_steps = self._load_sqlite_steps(session)
        elif session.source_format == "sqlite":
            raw_steps = self._load_sqlite_steps(session)
        else:
            raw_steps = self._load_jsonl_steps(session)

        return self.filter_normalized_steps(
            steps=raw_steps,
            since=since,
            until=until,
            since_last_user_input=since_last_user_input,
            include_step_types=include_step_types,
            include_actor_roles=include_actor_roles,
            exclude_actor_roles=exclude_actor_roles,
            include_thinking=include_thinking,
            include_raw_data=include_raw_data,
            max_step_chars=max_step_chars,
            offset=offset,
            from_end=from_end,
            limit=limit,
        )

    def _load_sqlite_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            conn = sqlite3.connect(f"file:{session.source_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM document")
            rows = cursor.fetchall()
            step_idx = 0

            for key, val_str in rows:
                if key == "opencode.global.dat:prompt-history":
                    try:
                        ph_data = json.loads(val_str)
                        entries = ph_data.get("entries", [])
                        for entry in entries:
                            prompts = entry.get("prompt", [])
                            prompt_texts = [
                                p.get("content", "") for p in prompts if isinstance(p, dict)
                            ]
                            combined = "\n".join(prompt_texts).strip()
                            if combined:
                                steps.append(
                                    NormalizedStep(
                                        step_index=step_idx,
                                        timestamp=session.last_activity,
                                        actor=Actor(role=ActorRole.USER),
                                        blocks=[TextBlock(text=combined)],
                                        harness_step_type="opencode_prompt_history",
                                    )
                                )
                                step_idx += 1
                    except Exception:
                        pass

            for key, val_str in rows:
                if session.session_id in key:
                    try:
                        v_json = json.loads(val_str)
                        prompts = v_json.get("prompt", [])
                        prompt_texts = [
                            p.get("content", "")
                            for p in prompts
                            if isinstance(p, dict) and p.get("content")
                        ]
                        combined = "\n".join(prompt_texts).strip()
                        if combined:
                            steps.append(
                                NormalizedStep(
                                    step_index=step_idx,
                                    timestamp=session.last_activity,
                                    actor=Actor(role=ActorRole.USER),
                                    blocks=[TextBlock(text=combined)],
                                    harness_step_type="opencode_active_prompt",
                                )
                            )
                            step_idx += 1
                    except Exception:
                        pass

            conn.close()
        except Exception:
            pass

        return steps

    def _load_jsonl_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        step_idx = 0
        with open(session.source_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                ts = normalize_timestamp(data.get("timestamp") or data.get("created_at"))
                role_str = data.get("role") or data.get("type") or "user"
                role = (
                    ActorRole.USER
                    if role_str == "user"
                    else ActorRole.ASSISTANT
                    if role_str == "assistant"
                    else ActorRole.SYSTEM
                )

                blocks: list[ContentBlock] = []
                content = data.get("content") or data.get("text") or ""

                if data.get("thinking"):
                    blocks.append(ThinkingBlock(text=data["thinking"]))

                if isinstance(content, str) and content:
                    blocks.append(TextBlock(text=content))
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "text":
                                blocks.append(TextBlock(text=b.get("text", "")))
                            elif b.get("type") == "tool_use":
                                blocks.append(
                                    ToolCallBlock(
                                        tool_name=b.get("name", "tool"),
                                        tool_args=b.get("input", {}),
                                    )
                                )
                            elif b.get("type") == "tool_result":
                                blocks.append(
                                    ToolResultBlock(content=str(b.get("content", "")))
                                )

                if not blocks:
                    blocks.append(TextBlock(text=""))

                steps.append(
                    NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(
                            role=role,
                            model=data.get("model") if role == ActorRole.ASSISTANT else None,
                        ),
                        blocks=blocks,
                        raw_data=data,
                        harness_step_type=f"opencode_{role_str}",
                    )
                )
                step_idx += 1

        return steps
