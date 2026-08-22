from __future__ import annotations

import glob
import logging
import os
import re
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.aider")
from codetalker.schema import (
    Actor,
    ActorRole,
    BlockType,
    CodeDiffBlock,
    ContentBlock,
    NormalizedSession,
    NormalizedStep,
    SystemEventBlock,
    TextBlock,
    ToolCallBlock,
)
from codetalker.utils.timestamps import normalize_timestamp


class AiderAdapter(BaseAdapter):
    """Adapter for Aider terminal pair programmer chat logs (.aider.chat.history.md)."""

    harness_name: str = "aider"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file() and (".aider.chat.history.md" in p.name or p.name.endswith(".md")):
                sess = self._inspect_history_file(str(p))
                if sess:
                    sessions.append(sess)
                return sessions
            elif p.is_dir():
                for f in p.glob("**/.aider.chat.history.md"):
                    sess = self._inspect_history_file(str(f))
                    if sess:
                        sessions.append(sess)
                return sessions

        # Look in current workspace / recent parent paths / user home
        candidates = [
            os.path.expanduser("~/.aider.chat.history.md"),
            os.path.abspath(".aider.chat.history.md"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                sess = self._inspect_history_file(c)
                if sess:
                    sessions.append(sess)

        return sessions

    def _inspect_history_file(self, file_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(file_path)
            last_activity = normalize_timestamp(mtime)

            cwd = os.path.dirname(os.path.abspath(file_path))
            session_id = f"aider_{abs(hash(file_path)) % 100000000:08d}"
            display_name: str | None = None
            started_at: str | None = None
            user_turns = 0
            assistant_turns = 0
            total_steps = 0

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("# aider chat started at "):
                        raw_date = line.replace("# aider chat started at ", "").strip()
                        ts = normalize_timestamp(raw_date)
                        if not started_at and ts:
                            started_at = ts
                    elif line.startswith("#### "):
                        user_turns += 1
                        total_steps += 1
                        if not display_name:
                            display_name = line.replace("#### ", "").strip()[:50]
                    elif line.startswith("> ") or line.startswith("```"):
                        if user_turns > assistant_turns:
                            assistant_turns += 1
                            total_steps += 1

            if not started_at:
                started_at = last_activity
            if not display_name:
                display_name = f"Aider Chat · {os.path.basename(cwd)}"

            return NormalizedSession(
                session_id=session_id,
                harness="aider",
                display_name=display_name,
                conversation_id=session_id,
                started_at=started_at,
                last_activity=last_activity,
                working_directory=cwd,
                model="Aider Agent",
                step_count=total_steps,
                user_turn_count=user_turns,
                assistant_turn_count=assistant_turns,
                source_path=file_path,
                source_format="markdown",
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
        raw_steps = self._load_history_steps(session)

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

    def _load_history_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            with open(session.source_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Split by markdown headers #### (user turns)
            chunks = re.split(r"(^####\s+.*$)", content, flags=re.MULTILINE)
            step_idx = 0

            # First chunk before any #### header is startup metadata
            if chunks and chunks[0].strip():
                init_text = chunks[0].strip()
                steps.append(
                    NormalizedStep(
                        step_index=step_idx,
                        timestamp=session.started_at,
                        actor=Actor(role=ActorRole.SYSTEM),
                        blocks=[SystemEventBlock(event_name="startup", detail=init_text)],
                        harness_step_type="startup",
                    )
                )
                step_idx += 1

            i = 1
            while i < len(chunks):
                header = chunks[i].strip()
                body = chunks[i + 1].strip() if i + 1 < len(chunks) else ""
                i += 2

                # User prompt in header
                user_prompt = header.replace("#### ", "").strip()
                steps.append(
                    NormalizedStep(
                        step_index=step_idx,
                        timestamp=session.last_activity,
                        actor=Actor(role=ActorRole.USER),
                        blocks=[TextBlock(text=user_prompt)],
                        harness_step_type="user_prompt",
                    )
                )
                step_idx += 1

                # Assistant response in body
                if body:
                    blocks: list[ContentBlock] = []
                    # Check for diff blocks <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
                    if "<<<<<<< SEARCH" in body and "=======" in body:
                        # Extract diff
                        diff_match = re.search(r"(<<<<<<< SEARCH.*?>>>>>>> REPLACE)", body, re.DOTALL)
                        if diff_match:
                            diff_str = diff_match.group(1)
                            text_without_diff = body.replace(diff_str, "").strip()
                            if text_without_diff:
                                blocks.append(TextBlock(text=text_without_diff))
                            blocks.append(CodeDiffBlock(file_uri="workspace_file", diff=diff_str))
                    if not blocks:
                        blocks.append(TextBlock(text=body))

                    steps.append(
                        NormalizedStep(
                            step_index=step_idx,
                            timestamp=session.last_activity,
                            actor=Actor(role=ActorRole.ASSISTANT, model="Aider"),
                            blocks=blocks,
                            harness_step_type="assistant_response",
                        )
                    )
                    step_idx += 1
        except Exception:
            pass

        return steps
