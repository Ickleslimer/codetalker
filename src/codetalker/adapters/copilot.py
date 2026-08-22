from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.copilot")
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
    ToolCallBlock,
    ToolResultBlock,
)
from codetalker.utils.timestamps import normalize_timestamp


class GitHubCopilotAdapter(BaseAdapter):
    """Adapter for GitHub Copilot Chat sessions in VSCode."""

    harness_name: str = "copilot"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file():
                if p.name.endswith(".jsonl") or p.name.endswith(".json"):
                    sess = self._inspect_copilot_json(str(p))
                    if sess:
                        sessions.append(sess)
                return sessions
            elif p.is_dir():
                for jf in p.glob("**/chatSessions/*.json*"):
                    sess = self._inspect_copilot_json(str(jf))
                    if sess:
                        sessions.append(sess)
                return sessions

        # Default paths to check:
        # %APPDATA%/Code/User/workspaceStorage/*/chatSessions/*.jsonl
        code_ws = os.path.expanduser("~/AppData/Roaming/Code/User/workspaceStorage")
        if os.path.isdir(code_ws):
            for pat in (
                os.path.join(code_ws, "*", "chatSessions", "*.jsonl"),
                os.path.join(code_ws, "*", "chatSessions", "*.json"),
            ):
                for fpath in glob.glob(pat):
                    sess = self._inspect_copilot_json(fpath)
                    if sess:
                        sessions.append(sess)

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _inspect_copilot_json(self, file_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(file_path)
            last_activity = normalize_timestamp(mtime)

            fname = os.path.basename(file_path)
            session_id = fname.split(".")[0]

            # Read workspace path from sibling workspace.json if present
            ws_dir = os.path.dirname(os.path.dirname(os.path.abspath(file_path)))
            ws_json = os.path.join(ws_dir, "workspace.json")
            working_dir: str | None = None
            if os.path.isfile(ws_json):
                try:
                    with open(ws_json, "r", encoding="utf-8") as f:
                        working_dir = json.load(f).get("folder")
                except Exception:
                    pass

            display_name: str | None = None
            started_at: str | None = None
            total_steps = 0
            user_turns = 0
            assistant_turns = 0
            model: str | None = None

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Handle JSON array or JSONL
            entries: list[dict[str, Any]] = []
            if content.strip().startswith("["):
                try:
                    entries = json.loads(content)
                except Exception:
                    pass
            elif content.strip().startswith("{"):
                try:
                    single = json.loads(content)
                    if "requests" in single:
                        # Full session JSON format
                        requests = single.get("requests", [])
                        for req in requests:
                            entries.append({"type": "request", **req})
                            if "response" in req:
                                entries.append({"type": "response", "response": req.get("response")})
                    else:
                        entries = [single]
                except Exception:
                    for line in content.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except Exception:
                                pass
            else:
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            pass

            for entry in entries:
                total_steps += 1
                ts = normalize_timestamp(entry.get("timestamp") or entry.get("createdAt"))
                if not started_at and ts:
                    started_at = ts
                if ts:
                    last_activity = ts

                etype = entry.get("type") or ("request" if "message" in entry else "response")
                if etype in ("request", "user"):
                    user_turns += 1
                    if not display_name:
                        msg = entry.get("message")
                        if isinstance(msg, dict):
                            display_name = msg.get("text", "")[:50].strip()
                        elif isinstance(msg, str):
                            display_name = msg[:50].strip()
                elif etype in ("response", "assistant"):
                    assistant_turns += 1
                    if not model and entry.get("model"):
                        model = entry.get("model")

            if not started_at:
                started_at = last_activity

            if not display_name:
                display_name = f"Copilot Chat {session_id[:8]}"

            return NormalizedSession(
                session_id=session_id,
                harness="copilot",
                display_name=display_name,
                conversation_id=session_id,
                branch_root_step_id=None,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                working_directory=working_dir,
                model=model or "GitHub Copilot Chat",
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
        include_thinking: bool = True,
        include_raw_data: bool = True,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        raw_steps = self._load_copilot_steps(session)

        return self.filter_normalized_steps(
            steps=raw_steps,
            since=since,
            until=until,
            since_last_user_input=since_last_user_input,
            include_step_types=include_step_types,
            include_actor_roles=include_actor_roles,
            include_thinking=include_thinking,
            include_raw_data=include_raw_data,
            limit=limit,
        )

    def _load_copilot_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            with open(session.source_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            entries: list[dict[str, Any]] = []
            if content.strip().startswith("["):
                try:
                    entries = json.loads(content)
                except Exception:
                    pass
            elif content.strip().startswith("{"):
                try:
                    single = json.loads(content)
                    if "requests" in single:
                        requests = single.get("requests", [])
                        for req in requests:
                            entries.append({"type": "request", **req})
                            if "response" in req:
                                entries.append({"type": "response", "response": req.get("response"), "timestamp": req.get("timestamp")})
                    else:
                        entries = [single]
                except Exception:
                    for line in content.splitlines():
                        if line.strip():
                            try:
                                entries.append(json.loads(line.strip()))
                            except Exception:
                                pass
            else:
                for line in content.splitlines():
                    if line.strip():
                        try:
                            entries.append(json.loads(line.strip()))
                        except Exception:
                            pass

            step_idx = 0
            for entry in entries:
                ts = normalize_timestamp(entry.get("timestamp") or entry.get("createdAt"))
                etype = entry.get("type") or ("request" if "message" in entry else "response")
                blocks: list[ContentBlock] = []

                if etype in ("request", "user"):
                    msg = entry.get("message")
                    user_text = ""
                    if isinstance(msg, dict):
                        user_text = msg.get("text", "")
                    elif isinstance(msg, str):
                        user_text = msg

                    blocks.append(TextBlock(text=user_text))

                    # References/attachments
                    for ref in entry.get("usedReferences", []):
                        uri = ref.get("reference", {}).get("value") or ref.get("uri")
                        if uri:
                            blocks.append(AttachmentBlock(attachment_type="file", url=str(uri)))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=ActorRole.USER),
                        blocks=blocks,
                        raw_data=entry,
                        harness_step_type="copilot_request",
                    )
                    steps.append(step)
                    step_idx += 1

                elif etype in ("response", "assistant"):
                    resp_data = entry.get("response")
                    ai_text = ""
                    if isinstance(resp_data, str):
                        ai_text = resp_data
                    elif isinstance(resp_data, list):
                        text_parts = []
                        for item in resp_data:
                            if isinstance(item, dict):
                                if "value" in item:
                                    text_parts.append(str(item["value"]))
                                elif "name" in item:
                                    blocks.append(
                                        ToolCallBlock(
                                            tool_name=item.get("name", "tool"),
                                            tool_args=item.get("parameters", {}),
                                        )
                                    )
                            elif isinstance(item, str):
                                text_parts.append(item)
                        ai_text = "\n".join(text_parts).strip()
                    elif isinstance(resp_data, dict):
                        ai_text = resp_data.get("value") or str(resp_data)

                    if ai_text or not blocks:
                        blocks.insert(0, TextBlock(text=ai_text))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=ActorRole.ASSISTANT, model=entry.get("model") or "GitHub Copilot"),
                        blocks=blocks,
                        raw_data=entry,
                        harness_step_type="copilot_response",
                    )
                    steps.append(step)
                    step_idx += 1
        except Exception:
            pass

        return steps
