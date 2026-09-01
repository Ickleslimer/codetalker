from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.freebuff")
from codetalker.schema import (
    Actor,
    ActorRole,
    AttachmentBlock,
    BlockType,
    BranchInfo,
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


class FreebuffAdapter(BaseAdapter):
    """Adapter for Freebuff / Codebuff Desktop agent conversations."""

    harness_name: str = "freebuff"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file() and (p.name.endswith(".db") or p.name.endswith(".sqlite")):
                sessions.extend(self._discover_from_db(str(p)))
                return sessions
            elif p.is_dir():
                for db_file in p.glob("**/desktop-v2.db"):
                    sessions.extend(self._discover_from_db(str(db_file)))
                return sessions

        # Default paths to check on Windows / macOS / Linux:
        # ~/.config/freebuff-desktop/projects/*/desktop-v2.db
        config_dir = os.path.expanduser("~/.config/freebuff-desktop/projects")
        if os.path.isdir(config_dir):
            for db_path in glob.glob(os.path.join(config_dir, "*", "desktop-v2.db")):
                sessions.extend(self._discover_from_db(db_path))

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _discover_from_db(self, db_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        if not os.path.isfile(db_path):
            return sessions

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Check if threads table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='threads'")
            if not cursor.fetchone():
                conn.close()
                return sessions

            cursor.execute(
                """
                SELECT 
                    id, project_path, title, model, created_at, updated_at, 
                    last_prompt_at, fork_source_thread_id, agent_mode
                FROM threads
                """
            )
            rows = cursor.fetchall()

            for row in rows:
                tid, ppath, title, model, cat, uat, lpat, fork_src, agent_mode = row
                if not tid:
                    continue

                started_at = normalize_timestamp(cat)
                last_activity = normalize_timestamp(uat or lpat or cat)

                # Get message counts
                cursor.execute(
                    "SELECT count(*), sum(case when role='user' then 1 else 0 end), sum(case when role='assistant' then 1 else 0 end) FROM messages WHERE thread_id = ?",
                    (tid,),
                )
                cnt_row = cursor.fetchone()
                total_steps = cnt_row[0] if cnt_row else 0
                user_turns = cnt_row[1] if cnt_row and cnt_row[1] else 0
                assistant_turns = cnt_row[2] if cnt_row and cnt_row[2] else 0

                display_name = title or f"Freebuff Session {tid[:8]}"
                root_conv_id = fork_src if fork_src else tid

                sess = NormalizedSession(
                    session_id=tid,
                    harness="freebuff",
                    display_name=display_name,
                    conversation_id=root_conv_id,
                    branch_root_step_id=fork_src,
                    branch_label="Forked Thread" if fork_src else "Main Thread",
                    started_at=started_at,
                    last_activity=last_activity,
                    working_directory=ppath,
                    model=model or f"Freebuff ({agent_mode})",
                    step_count=total_steps,
                    user_turn_count=user_turns,
                    assistant_turn_count=assistant_turns,
                    source_path=db_path,
                    source_format="sqlite",
                    has_dag=bool(fork_src),
                )
                sessions.append(sess)

            conn.close()
        except Exception as e:
            logger.warning(f"Error reading Freebuff SQLite database '{db_path}': {e}", exc_info=True)

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
        raw_steps = self._load_thread_steps(session)

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

    def _load_thread_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            conn = sqlite3.connect(f"file:{session.source_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT seq, role, parts_json, attachments_json, metrics_json, ts
                FROM messages
                WHERE thread_id = ?
                ORDER BY seq ASC
                """,
                (session.session_id,),
            )
            rows = cursor.fetchall()

            step_idx = 0
            for seq, role_str, pjson, ajson, mjson, ts_raw in rows:
                ts = normalize_timestamp(ts_raw)

                # Map role
                if role_str == "user":
                    role = ActorRole.USER
                elif role_str == "assistant":
                    role = ActorRole.ASSISTANT
                elif role_str == "tool":
                    role = ActorRole.TOOL
                else:
                    role = ActorRole.SYSTEM

                blocks: list[ContentBlock] = []

                # Parse parts
                try:
                    parts = json.loads(pjson) if pjson else []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        pkind = part.get("kind")
                        if pkind == "text":
                            t_val = part.get("text", "")
                            if t_val:
                                blocks.append(TextBlock(text=t_val))
                        elif pkind == "reasoning":
                            r_val = part.get("text", "")
                            if r_val:
                                blocks.append(ThinkingBlock(text=r_val))
                        elif pkind == "tool":
                            blocks.append(
                                ToolCallBlock(
                                    tool_name=part.get("toolName") or "tool",
                                    tool_args=part.get("input") or {},
                                )
                            )
                        elif pkind == "tool_result":
                            blocks.append(
                                ToolResultBlock(
                                    content=str(part.get("output") or part.get("result") or ""),
                                    is_error=bool(part.get("isError")),
                                )
                            )
                except Exception as e:
                    logger.debug(f"Failed parsing parts_json for Freebuff seq {seq}: {e}")
                    if pjson:
                        blocks.append(TextBlock(text=pjson))

                # Parse attachments
                try:
                    attachments = json.loads(ajson) if ajson else []
                    for att in attachments:
                        if isinstance(att, dict):
                            a_path = att.get("path") or att.get("url") or att.get("name")
                            a_kind = att.get("kind") or "file"
                            if a_path:
                                blocks.append(AttachmentBlock(attachment_type=a_kind, url=str(a_path)))
                except Exception as e:
                    logger.debug(f"Failed parsing attachments_json for Freebuff seq {seq}: {e}")

                if not blocks:
                    blocks.append(TextBlock(text=""))

                raw_entry = {"seq": seq, "role": role_str, "metrics": mjson}

                step = NormalizedStep(
                    step_index=step_idx,
                    timestamp=ts or session.last_activity,
                    actor=Actor(role=role, model=session.model if role == ActorRole.ASSISTANT else None),
                    blocks=blocks,
                    branch=BranchInfo(step_id=f"seq_{seq}"),
                    raw_data=raw_entry,
                    harness_step_type=f"freebuff_{role_str}",
                )
                steps.append(step)
                step_idx += 1

            conn.close()
        except Exception as e:
            logger.warning(f"Error loading Freebuff thread steps from '{session.source_path}': {e}", exc_info=True)

        return steps
