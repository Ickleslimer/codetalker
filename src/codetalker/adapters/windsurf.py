from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.windsurf")
from codetalker.schema import (
    Actor,
    ActorRole,
    AttachmentBlock,
    BlockType,
    ContentBlock,
    NormalizedSession,
    NormalizedStep,
    SystemEventBlock,
    TextBlock,
)
from codetalker.utils.timestamps import normalize_timestamp
from codetalker.utils.paths import normalize_working_directory
from codetalker.utils.display import clip_display_name
from codetalker.utils.sanitize import sanitize_protobuf_text


def _decode_protobuf_wire(data: bytes) -> list[tuple[int, str, Any]]:
    """Decode raw protobuf wire stream into (field_number, wire_type, value) tuples."""
    pos = 0
    length = len(data)
    fields: list[tuple[int, str, Any]] = []

    while pos < length:
        # Read varint tag
        tag = 0
        shift = 0
        while True:
            if pos >= length:
                return fields
            b = data[pos]
            pos += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7

        field_num = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # Varint
            val = 0
            shift = 0
            while True:
                if pos >= length:
                    break
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            fields.append((field_num, "varint", val))
        elif wire_type == 1:  # 64-bit
            val_bytes = data[pos : pos + 8]
            pos += 8
            fields.append((field_num, "64bit", val_bytes))
        elif wire_type == 2:  # Length-delimited
            vlen = 0
            shift = 0
            while True:
                if pos >= length:
                    break
                b = data[pos]
                pos += 1
                vlen |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            val_bytes = data[pos : pos + vlen]
            pos += vlen

            # Check if valid UTF-8 text with high printable ratio
            is_text = False
            try:
                text = val_bytes.decode("utf-8")
                if text:
                    printable_count = sum(1 for c in text if 32 <= ord(c) <= 126 or c in "\n\r\t" or ord(c) > 127)
                    if printable_count >= len(text) * 0.9:
                        is_text = True
                        fields.append((field_num, "string", text))
            except UnicodeDecodeError:
                pass

            if not is_text:
                nested = _decode_protobuf_wire(val_bytes)
                if nested and len(nested) > 0:
                    fields.append((field_num, "nested", nested))
                else:
                    fields.append((field_num, "bytes", val_bytes))
        elif wire_type == 5:  # 32-bit
            val_bytes = data[pos : pos + 4]
            pos += 4
            fields.append((field_num, "32bit", val_bytes))
        else:
            break

    return fields


def _extract_all_strings(fields: list[tuple[int, str, Any]]) -> list[str]:
    """Recursively collect non-empty strings from decoded protobuf fields."""
    res: list[str] = []
    for _, ftype, val in fields:
        if ftype == "string" and val and val.strip():
            res.append(val.strip())
        elif ftype == "nested" and isinstance(val, list):
            res.extend(_extract_all_strings(val))
    return res


class WindsurfAdapter(BaseAdapter):
    """Adapter for Devin / Windsurf (Codeium) agent and chat sessions."""

    harness_name: str = "windsurf"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file():
                if p.name.endswith(".pb"):
                    sess = self._inspect_pb_file(str(p))
                    if sess:
                        sessions.append(sess)
                elif p.name.endswith(".vscdb") or p.name.endswith(".sqlite"):
                    sessions.extend(self._discover_vscdb(str(p)))
                return sessions
            elif p.is_dir():
                for pb_file in p.glob("**/*.pb"):
                    if "chat_state" in str(pb_file) or "codeium_chat_state" in pb_file.name:
                        sess = self._inspect_pb_file(str(pb_file))
                        if sess:
                            sessions.append(sess)
                for db_file in p.glob("**/state.vscdb"):
                    sessions.extend(self._discover_vscdb(str(db_file)))
                return sessions

        # Default paths to check on disk:
        # 1. ~/.codeium/chat_state/*.pb
        chat_state_dir = os.path.expanduser("~/.codeium/chat_state")
        if os.path.isdir(chat_state_dir):
            for pb_path in glob.glob(os.path.join(chat_state_dir, "*.pb")):
                sess = self._inspect_pb_file(pb_path)
                if sess:
                    sessions.append(sess)

        # 2. %APPDATA%/Devin/User/workspaceStorage/*/state.vscdb
        devin_ws = os.path.expanduser("~/AppData/Roaming/Devin/User/workspaceStorage")
        if os.path.isdir(devin_ws):
            for db_path in glob.glob(os.path.join(devin_ws, "*", "state.vscdb")):
                sessions.extend(self._discover_vscdb(db_path))

        # 3. %APPDATA%/Windsurf/User/workspaceStorage/*/state.vscdb
        windsurf_ws = os.path.expanduser("~/AppData/Roaming/Windsurf/User/workspaceStorage")
        if os.path.isdir(windsurf_ws):
            for db_path in glob.glob(os.path.join(windsurf_ws, "*", "state.vscdb")):
                sessions.extend(self._discover_vscdb(db_path))

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _decode_cwd_from_filename(self, filename: str) -> str | None:
        """Extract workspace path from codeium_chat_state_file_<encoded>.pb."""
        # e.g. codeium_chat_state_file_c_3A_Users_username_Documents_project.pb
        prefix = "codeium_chat_state_file_"
        if filename.startswith(prefix) and filename.endswith(".pb"):
            raw = filename[len(prefix) : -3]
            # Replace _3A_ with :/ and _ with /
            raw = raw.replace("_3A_", ":/").replace("_3a_", ":/")
            parts = raw.split("_")
            if len(parts) > 1 and ":" in parts[0]:
                return parts[0] + "/" + "/".join(parts[1:])
            return "/".join(parts)
        return None

    def _inspect_pb_file(self, pb_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(pb_path)
            last_activity = normalize_timestamp(mtime)
            filename = os.path.basename(pb_path)
            cwd = self._decode_cwd_from_filename(filename)

            with open(pb_path, "rb") as f:
                data = f.read()

            parsed = _decode_protobuf_wire(data)
            turns = [v for fn, ftype, v in parsed if fn == 1 and ftype == "nested"]

            total_steps = len(turns)
            user_turn_count = 0
            assistant_turn_count = 0
            first_user_prompt: str | None = None
            started_at: str | None = None

            for turn in turns:
                all_strs = _extract_all_strings(turn)
                is_user = any(s.startswith("user-") for s in all_strs)
                is_bot = any(s.startswith("bot-") for s in all_strs)

                if is_user:
                    user_turn_count += 1
                    if not first_user_prompt:
                        # Find the first meaningful user prompt text
                        for s in all_strs:
                            clean = s.strip()
                            if (
                                not clean.startswith("user-")
                                and not clean.startswith("bot-")
                                and not clean.startswith("status-")
                                and "file:/" not in clean
                                and not (len(clean) == 32 and clean.isalnum())
                                and len(clean) > 8
                            ):
                                first_user_prompt = clean
                                break
                elif is_bot:
                    assistant_turn_count += 1

                # Check for timestamp in turn fields
                for fn, ftype, v in turn:
                    if fn == 3 and ftype == "nested":
                        # Subfields 1 (sec) and 2 (nanos)
                        sec = None
                        for sfn, sftype, sv in v:
                            if sfn == 1 and sftype == "varint":
                                sec = sv
                        if sec and not started_at:
                            started_at = normalize_timestamp(sec)

            if not started_at:
                started_at = last_activity

            session_id = filename.replace(".pb", "")
            display_name, truncated = clip_display_name(
                first_user_prompt.strip() if first_user_prompt else None
            )
            if not display_name and cwd:
                display_name = f"Devin Chat · {os.path.basename(cwd)}"
            if not display_name:
                display_name = f"Devin Session {session_id[:12]}"

            return NormalizedSession(
                session_id=session_id,
                harness="windsurf",
                display_name=display_name,
                conversation_id=session_id,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                working_directory=normalize_working_directory(cwd),
                model="Devin (Windsurf Cascade)",
                step_count=total_steps,
                user_turn_count=user_turn_count,
                assistant_turn_count=assistant_turn_count,
                source_path=pb_path,
                source_format="protobuf",
                has_dag=False,
                display_name_truncated=truncated,
            )
        except Exception:
            return None

    def _discover_vscdb(self, db_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        if not os.path.isfile(db_path):
            return sessions

        ws_folder: str | None = None
        ws_json = os.path.join(os.path.dirname(db_path), "workspace.json")
        if os.path.isfile(ws_json):
            try:
                with open(ws_json, "r", encoding="utf-8") as f:
                    ws_data = json.load(f)
                    ws_folder = ws_data.get("folder")
            except Exception:
                pass

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE 'agentSessions.state.cache' OR key LIKE 'cascade.sessionData%'"
            )
            rows = cursor.fetchall()
            mtime = os.path.getmtime(db_path)
            last_activity = normalize_timestamp(mtime)

            for key, val_str in rows:
                if not val_str or val_str == "[]" or val_str == "{}":
                    continue
                try:
                    data = json.loads(val_str)
                    if isinstance(data, list):
                        for idx, item in enumerate(data):
                            sid = item.get("id") or f"devin_session_{idx}"
                            title = item.get("title") or item.get("name") or "Devin Workspace Session"
                            sess = NormalizedSession(
                                session_id=sid,
                                harness="windsurf",
                                display_name=title,
                                conversation_id=sid,
                                started_at=last_activity,
                                last_activity=last_activity,
                                working_directory=ws_folder,
                                source_path=db_path,
                                source_format="sqlite",
                                has_dag=False,
                            )
                            sessions.append(sess)
                    elif isinstance(data, dict):
                        sid = data.get("sessionId") or data.get("id") or "devin_workspace_session"
                        title = data.get("title") or "Devin Workspace Session"
                        sess = NormalizedSession(
                            session_id=sid,
                            harness="windsurf",
                            display_name=title,
                            conversation_id=sid,
                            started_at=normalize_timestamp(data.get("createdAt")) or last_activity,
                            last_activity=normalize_timestamp(data.get("lastActiveAt")) or last_activity,
                            working_directory=ws_folder,
                            source_path=db_path,
                            source_format="sqlite",
                            has_dag=False,
                        )
                        sessions.append(sess)
                except Exception:
                    continue
            conn.close()
        except Exception:
            pass

        return sessions

    # ─── Loading Steps ────────────────────────────────────────────────────────

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
        if session.source_format == "protobuf":
            raw_steps = self._load_protobuf_steps(session)
        elif session.source_format == "sqlite":
            raw_steps = self._load_sqlite_steps(session)
        else:
            raw_steps = []

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

    def _load_protobuf_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            with open(session.source_path, "rb") as f:
                data = f.read()

            parsed = _decode_protobuf_wire(data)
            turns = [v for fn, ftype, v in parsed if fn == 1 and ftype == "nested"]

            step_idx = 0
            for turn in turns:
                all_strs = _extract_all_strings(turn)
                is_user = any(s.startswith("user-") for s in all_strs)
                is_bot = any(s.startswith("bot-") for s in all_strs)
                is_status = any(s.startswith("status-") for s in all_strs)

                # Extract timestamp
                step_ts: str | None = None
                for fn, ftype, v in turn:
                    if fn == 3 and ftype == "nested":
                        sec = None
                        for sfn, sftype, sv in v:
                            if sfn == 1 and sftype == "varint":
                                sec = sv
                        if sec:
                            step_ts = normalize_timestamp(sec)

                blocks: list[ContentBlock] = []

                if is_user:
                    user_texts: list[str] = []
                    attachments: list[AttachmentBlock] = []

                    for s in all_strs:
                        clean = s.strip()
                        if (
                            clean.startswith("user-")
                            or clean.startswith("bot-")
                            or clean.startswith("status-")
                            or (len(clean) == 32 and clean.isalnum())
                        ):
                            continue
                        elif clean.startswith("file:///") or clean.startswith("%file:///"):
                            url_clean = clean.lstrip("%")
                            attachments.append(AttachmentBlock(attachment_type="file", url=url_clean))
                        elif not clean.startswith("http://") and not clean.startswith("https://") and len(clean) > 1:
                            user_texts.append(clean)

                    prompt_text = "\n".join(user_texts).strip()
                    if prompt_text:
                        blocks.append(TextBlock(text=sanitize_protobuf_text(prompt_text)))
                    for att in attachments:
                        blocks.append(att)

                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=step_ts or session.last_activity,
                        actor=Actor(role=ActorRole.USER),
                        blocks=blocks,
                        harness_step_type="user_turn",
                    )
                    steps.append(step)
                    step_idx += 1

                elif is_bot:
                    bot_texts: list[str] = []
                    context_files: list[str] = []

                    for s in all_strs:
                        clean = s.strip()
                        if (
                            clean.startswith("user-")
                            or clean.startswith("bot-")
                            or clean.startswith("status-")
                            or (len(clean) == 32 and clean.isalnum())
                        ):
                            continue
                        elif clean.startswith("file:///") or clean.startswith("%file:///"):
                            url_clean = clean.lstrip("%")
                            context_files.append(url_clean)
                        elif len(clean) > 1 and not clean.startswith("CONTEXT_SNIPPET_"):
                            bot_texts.append(clean)

                    response_text = "\n".join(bot_texts).strip()
                    if response_text:
                        blocks.append(TextBlock(text=sanitize_protobuf_text(response_text)))
                    for cf in context_files:
                        blocks.append(AttachmentBlock(attachment_type="file", url=cf))

                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=step_ts or session.last_activity,
                        actor=Actor(role=ActorRole.ASSISTANT, model="Devin (Windsurf)"),
                        blocks=blocks,
                        harness_step_type="assistant_turn",
                    )
                    steps.append(step)
                    step_idx += 1

                elif is_status:
                    status_text = " ".join(s for s in all_strs if not s.startswith("status-"))
                    blocks.append(
                        SystemEventBlock(
                            event_name="status_update",
                            detail=status_text or "Done",
                        )
                    )
                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=step_ts or session.last_activity,
                        actor=Actor(role=ActorRole.SYSTEM),
                        blocks=blocks,
                        harness_step_type="status",
                    )
                    steps.append(step)
                    step_idx += 1

        except Exception:
            pass

        return steps

    def _load_sqlite_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        # Basic SQLite step loader for state.vscdb
        steps: list[NormalizedStep] = []
        return steps
