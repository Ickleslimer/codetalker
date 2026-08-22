from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.claude")
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
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from codetalker.utils.timestamps import normalize_timestamp


class ClaudeCodeAdapter(BaseAdapter):
    """Adapter for Anthropic Claude Code terminal agent sessions."""

    harness_name: str = "claude"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file() and p.name.endswith(".jsonl"):
                sess = self._inspect_session_file(str(p))
                if sess:
                    sessions.append(sess)
                return sessions
            elif p.is_dir():
                for jf in p.glob("**/*.jsonl"):
                    sess = self._inspect_session_file(str(jf))
                    if sess:
                        sessions.append(sess)
                return sessions

        # Default paths to check:
        # ~/.claude/projects/*/sessions/*.jsonl
        # ~/.claude/sessions/*.jsonl
        base_claude = os.path.expanduser("~/.claude")
        if os.path.isdir(base_claude):
            patterns = [
                os.path.join(base_claude, "projects", "*", "sessions", "*.jsonl"),
                os.path.join(base_claude, "projects", "*", "*.jsonl"),
                os.path.join(base_claude, "sessions", "*.jsonl"),
            ]
            for pat in patterns:
                for fpath in glob.glob(pat):
                    sess = self._inspect_session_file(fpath)
                    if sess:
                        sessions.append(sess)

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _inspect_session_file(self, file_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(file_path)
            last_activity = normalize_timestamp(mtime)

            fname = os.path.basename(file_path)
            session_id = fname.replace(".jsonl", "")

            # Infer project name from folder if under projects/<slug>/
            parts = Path(file_path).parts
            project_name: str | None = None
            if "projects" in parts:
                idx = parts.index("projects")
                if idx + 1 < len(parts):
                    project_name = parts[idx + 1]

            display_name: str | None = None
            started_at: str | None = None
            total_steps = 0
            user_turns = 0
            assistant_turns = 0
            model: str | None = None

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f):
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

                        msg = data.get("message") or data
                        role = msg.get("role") or data.get("type")

                        if role == "user":
                            user_turns += 1
                            if not display_name:
                                content = msg.get("content")
                                if isinstance(content, str):
                                    display_name = content[:50].strip().replace("\n", " ")
                                elif isinstance(content, list):
                                    for b in content:
                                        if isinstance(b, dict) and b.get("type") == "text":
                                            display_name = b.get("text", "")[:50].strip().replace("\n", " ")
                                            break
                        elif role == "assistant":
                            assistant_turns += 1
                            if not model and msg.get("model"):
                                model = msg.get("model")
                    except Exception:
                        continue

            if not started_at:
                started_at = last_activity

            if not display_name:
                if project_name:
                    display_name = f"Claude Code · {project_name}"
                else:
                    display_name = f"Claude Session {session_id[:8]}"

            return NormalizedSession(
                session_id=session_id,
                harness="claude",
                display_name=display_name,
                conversation_id=session_id,
                branch_root_step_id=None,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                working_directory=project_name,
                model=model or "Claude 3.7 Sonnet (Claude Code)",
                step_count=total_steps,
                user_turn_count=user_turns,
                assistant_turn_count=assistant_turns,
                source_path=file_path,
                source_format="jsonl",
                has_dag=False,
            )
        except Exception:
            return None

    # ─── Loading Steps ────────────────────────────────────────────────────────

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
        raw_steps = self._load_session_steps(session)

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

    def _load_session_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
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
                msg = data.get("message") or data
                role_raw = msg.get("role") or data.get("type") or "system"
                content_raw = msg.get("content")

                blocks: list[ContentBlock] = []

                if role_raw == "user":
                    role = ActorRole.USER
                    if isinstance(content_raw, str):
                        blocks.append(TextBlock(text=content_raw))
                    elif isinstance(content_raw, list):
                        for b in content_raw:
                            if isinstance(b, dict):
                                btype = b.get("type")
                                if btype == "text":
                                    blocks.append(TextBlock(text=b.get("text", "")))
                                elif btype == "tool_result":
                                    blocks.append(
                                        ToolResultBlock(
                                            content=str(b.get("content", "")),
                                            is_error=b.get("is_error", False),
                                        )
                                    )
                                elif btype == "image":
                                    blocks.append(AttachmentBlock(attachment_type="image", url=b.get("source", {}).get("data")))
                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=role),
                        blocks=blocks,
                        raw_data=data,
                        harness_step_type="user_message",
                    )
                    steps.append(step)
                    step_idx += 1

                elif role_raw == "assistant":
                    role = ActorRole.ASSISTANT
                    model_name = msg.get("model") or "Claude Code"
                    if isinstance(content_raw, str):
                        blocks.append(TextBlock(text=content_raw))
                    elif isinstance(content_raw, list):
                        for b in content_raw:
                            if isinstance(b, dict):
                                btype = b.get("type")
                                if btype == "thinking":
                                    blocks.append(
                                        ThinkingBlock(
                                            text=b.get("thinking", ""),
                                            has_signature=bool(b.get("signature")),
                                        )
                                    )
                                elif btype == "text":
                                    blocks.append(TextBlock(text=b.get("text", "")))
                                elif btype == "tool_use":
                                    blocks.append(
                                        ToolCallBlock(
                                            tool_name=b.get("name", "tool"),
                                            tool_args=b.get("input", {}),
                                        )
                                    )
                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=role, model=model_name),
                        blocks=blocks,
                        raw_data=data,
                        harness_step_type="assistant_message",
                    )
                    steps.append(step)
                    step_idx += 1

                elif role_raw in ("system", "progress", "tool_result"):
                    role = ActorRole.SYSTEM if role_raw != "tool_result" else ActorRole.TOOL
                    if isinstance(content_raw, str):
                        blocks.append(SystemEventBlock(event_name=role_raw, detail=content_raw))
                    elif isinstance(content_raw, list):
                        for b in content_raw:
                            if isinstance(b, dict) and b.get("type") == "tool_result":
                                blocks.append(
                                    ToolResultBlock(
                                        content=str(b.get("content", "")),
                                        is_error=b.get("is_error", False),
                                    )
                                )
                    if not blocks:
                        blocks.append(SystemEventBlock(event_name=role_raw, detail=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=role),
                        blocks=blocks,
                        raw_data=data,
                        harness_step_type=role_raw,
                    )
                    steps.append(step)
                    step_idx += 1

        return steps
