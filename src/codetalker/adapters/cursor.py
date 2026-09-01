from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.cursor")
from codetalker.schema import (
    Actor,
    ActorRole,
    AttachmentBlock,
    BlockType,
    CodeDiffBlock,
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
from codetalker.utils.paths import normalize_working_directory
from codetalker.utils.display import clip_display_name


class CursorAdapter(BaseAdapter):
    """Adapter for Cursor IDE Composer and AI Chat sessions."""

    harness_name: str = "cursor"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file() and (p.name.endswith(".vscdb") or p.name.endswith(".sqlite")):
                sessions.extend(self._discover_from_db(str(p)))
                return sessions
            elif p.is_dir():
                for db_file in p.glob("**/state.vscdb"):
                    sessions.extend(self._discover_from_db(str(db_file)))
                return sessions

        # Default paths to check on Windows / macOS / Linux:
        # 1. Global storage: %APPDATA%/Cursor/User/globalStorage/state.vscdb
        global_db = os.path.expanduser("~/AppData/Roaming/Cursor/User/globalStorage/state.vscdb")
        if os.path.isfile(global_db):
            sessions.extend(self._discover_from_global_db(global_db))

        # 2. Workspace storages: %APPDATA%/Cursor/User/workspaceStorage/*/state.vscdb
        ws_dir = os.path.expanduser("~/AppData/Roaming/Cursor/User/workspaceStorage")
        if os.path.isdir(ws_dir):
            for db_path in glob.glob(os.path.join(ws_dir, "*", "state.vscdb")):
                sessions.extend(self._discover_from_workspace_db(db_path))

        # Deduplicate sessions by session_id
        seen_ids = set()
        unique_sessions: list[NormalizedSession] = []
        for s in sessions:
            if s.session_id not in seen_ids:
                seen_ids.add(s.session_id)
                unique_sessions.append(s)

        # Sort descending by last_activity
        unique_sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return unique_sessions

    def count_steps(self, session: NormalizedSession) -> int:
        stats = self._peek_composer_stats(session.session_id, session.source_path)
        if stats:
            return stats[0]
        return len(self._load_composer_steps(session))

    @staticmethod
    def _peek_composer_stats(
        composer_id: str, source_path: str
    ) -> tuple[int, int, int] | None:
        """Return (step_count, user_turns, assistant_turns) from composer headers only."""
        global_db = os.path.expanduser("~/AppData/Roaming/Cursor/User/globalStorage/state.vscdb")
        db_to_use = source_path if os.path.isfile(source_path) else global_db
        if not os.path.isfile(db_to_use):
            return None
        try:
            conn = sqlite3.connect(f"file:{db_to_use}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ? OR key = ?",
                (f"composerData:{composer_id}", f"composerData:task-{composer_id}"),
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None
            cdata = json.loads(row[0])
            headers = cdata.get("fullConversationHeadersOnly") or []
            user_turns = sum(1 for h in headers if h.get("type") == 1)
            assistant_turns = sum(1 for h in headers if h.get("type") == 2)
            return len(headers), user_turns, assistant_turns
        except Exception:
            return None

    def _discover_from_global_db(self, db_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Check if composerHeaders table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='composerHeaders'")
            if cursor.fetchone():
                cursor.execute(
                    "SELECT composerId, createdAt, lastUpdatedAt, value FROM composerHeaders"
                )
                for cid, created_at, updated_at, header_raw in cursor.fetchall():
                    if not cid:
                        continue
                    display_name: str | None = None
                    cwd: str | None = None
                    model: str | None = None
                    step_count = 0
                    user_turn_count = 0
                    assistant_turn_count = 0
                    mode: str | None = None

                    if header_raw:
                        try:
                            hdata = json.loads(header_raw)
                            display_name = hdata.get("name")
                            mode = hdata.get("unifiedMode")
                            if not display_name and hdata.get("subtitle"):
                                display_name = hdata.get("subtitle")
                            ws_ident = hdata.get("workspaceIdentifier") or {}
                            cwd = ws_ident.get("uri", {}).get("fsPath") or ws_ident.get("uri", {}).get("external")
                        except Exception:
                            pass

                    if not display_name:
                        display_name = f"Cursor Composer {cid[:8]}"

                    display_name, truncated = clip_display_name(display_name)
                    stats = self._peek_composer_stats(cid, db_path)
                    if stats:
                        step_count, user_turn_count, assistant_turn_count = stats

                    started_at = normalize_timestamp(created_at)
                    last_activity = normalize_timestamp(updated_at or created_at)

                    model_name = f"Cursor ({mode})" if mode else "Cursor Composer"

                    sess = NormalizedSession(
                        session_id=cid,
                        harness="cursor",
                        display_name=display_name,
                        conversation_id=cid,
                        branch_root_step_id=None,
                        branch_label="Main Thread",
                        started_at=started_at,
                        last_activity=last_activity,
                        working_directory=normalize_working_directory(cwd),
                        model=model_name,
                        step_count=step_count,
                        user_turn_count=user_turn_count,
                        assistant_turn_count=assistant_turn_count,
                        source_path=db_path,
                        source_format="sqlite",
                        has_dag=False,
                        display_name_truncated=truncated,
                    )
                    sessions.append(sess)

            conn.close()
        except Exception as e:
            logger.warning(f"Error inspecting Cursor global DB '{db_path}': {e}", exc_info=True)

        return sessions

    def _discover_from_workspace_db(self, db_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        try:
            ws_folder: str | None = None
            ws_json = os.path.join(os.path.dirname(db_path), "workspace.json")
            if os.path.isfile(ws_json):
                try:
                    with open(ws_json, "r", encoding="utf-8") as f:
                        ws_folder = json.load(f).get("folder")
                except Exception:
                    pass

            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM ItemTable WHERE key = 'composer.composerData'")
            row = cursor.fetchone()
            if row and row[1]:
                cdata = json.loads(row[1])
                composers = cdata.get("allComposers") or []
                for comp in composers:
                    cid = comp.get("composerId")
                    if not cid:
                        continue
                    title = comp.get("name") or comp.get("subtitle") or f"Cursor Session {cid[:8]}"
                    title, truncated = clip_display_name(title)
                    created_at = normalize_timestamp(comp.get("createdAt"))
                    last_act = normalize_timestamp(comp.get("lastUpdatedAt")) or created_at
                    mode = comp.get("unifiedMode")
                    stats = self._peek_composer_stats(cid, db_path)
                    step_count = stats[0] if stats else 0
                    user_turn_count = stats[1] if stats else 0
                    assistant_turn_count = stats[2] if stats else 0
                    sess = NormalizedSession(
                        session_id=cid,
                        harness="cursor",
                        display_name=title,
                        conversation_id=cid,
                        started_at=created_at,
                        last_activity=last_act,
                        working_directory=normalize_working_directory(ws_folder),
                        model=f"Cursor ({mode})" if mode else "Cursor Composer",
                        step_count=step_count,
                        user_turn_count=user_turn_count,
                        assistant_turn_count=assistant_turn_count,
                        source_path=db_path,
                        source_format="sqlite",
                        has_dag=False,
                        display_name_truncated=truncated,
                    )
                    sessions.append(sess)
            conn.close()
        except Exception:
            pass

        return sessions

    def _discover_from_db(self, db_path: str) -> list[NormalizedSession]:
        res = self._discover_from_global_db(db_path)
        if not res:
            res = self._discover_from_workspace_db(db_path)
        return res

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
        raw_steps = self._load_composer_steps(session)

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

    def _load_composer_steps(self, session: NormalizedSession) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        cid = session.session_id

        global_db = os.path.expanduser("~/AppData/Roaming/Cursor/User/globalStorage/state.vscdb")
        db_to_use = session.source_path if os.path.isfile(session.source_path) else global_db

        if not os.path.isfile(db_to_use):
            return steps

        try:
            conn = sqlite3.connect(f"file:{db_to_use}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Retrieve composerData
            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ? OR key = ?",
                (f"composerData:{cid}", f"composerData:task-{cid}"),
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return steps

            cdata = json.loads(row[0])
            headers = cdata.get("fullConversationHeadersOnly") or []
            conv_map = cdata.get("conversationMap") or {}

            step_idx = 0
            for h in headers:
                bid = h.get("bubbleId")
                btype = h.get("type")  # 1 = user, 2 = ai
                ts = normalize_timestamp(h.get("createdAt"))
                grouping = h.get("grouping") or {}

                # Attempt to read full bubble data
                bubble_data: dict[str, Any] | None = None
                if bid and conv_map and bid in conv_map:
                    bubble_data = conv_map[bid]
                elif bid:
                    # Query bubbleId from cursorDiskKV
                    cursor.execute(
                        "SELECT value FROM cursorDiskKV WHERE key = ? OR key = ?",
                        (f"bubbleId:{cid}:{bid}", f"bubbleId:{bid}"),
                    )
                    brow = cursor.fetchone()
                    if brow:
                        try:
                            bubble_data = json.loads(brow[0])
                        except Exception:
                            pass

                blocks: list[ContentBlock] = []

                if btype == 1:  # USER turn
                    role = ActorRole.USER
                    user_text = ""
                    if bubble_data:
                        user_text = bubble_data.get("text") or ""
                        if not user_text and bubble_data.get("richText"):
                            try:
                                rt = json.loads(bubble_data["richText"]) if isinstance(bubble_data["richText"], str) else bubble_data["richText"]
                                # extract plain text from lexical structure
                                user_text = str(rt)
                            except Exception:
                                user_text = str(bubble_data["richText"])
                    if not user_text:
                        user_text = grouping.get("textPreview") or ""

                    blocks.append(TextBlock(text=user_text))

                    # Attached code chunks / files
                    if bubble_data:
                        for chunk in bubble_data.get("attachedCodeChunks", []):
                            uri = chunk.get("relativeWorkspacePath") or chunk.get("uri", {}).get("fsPath")
                            if uri:
                                blocks.append(AttachmentBlock(attachment_type="file", url=uri))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=role),
                        blocks=blocks,
                        harness_step_type="user_bubble",
                    )
                    steps.append(step)
                    step_idx += 1

                elif btype == 2:  # ASSISTANT turn
                    role = ActorRole.ASSISTANT
                    # 1. Check for thinking
                    if grouping.get("hasThinking") or (bubble_data and bubble_data.get("thinking")):
                        thinking_raw = (bubble_data.get("thinking") if bubble_data else None) or f"Thinking ({grouping.get('thinkingDurationMs', 0)}ms)"
                        thinking_text = thinking_raw
                        if isinstance(thinking_raw, str) and thinking_raw.strip().startswith("{"):
                            try:
                                parsed = json.loads(thinking_raw)
                                if isinstance(parsed, dict) and parsed.get("text"):
                                    thinking_text = str(parsed["text"])
                            except Exception:
                                pass
                        blocks.append(ThinkingBlock(text=thinking_text))

                    # 2. Check for assistant text
                    ai_text = ""
                    if bubble_data:
                        ai_text = bubble_data.get("text") or ""
                    if not ai_text:
                        ai_text = grouping.get("textPreview") or ""

                    if ai_text:
                        blocks.append(TextBlock(text=ai_text))

                    # 3. Check for tool calls or diffs
                    tool_case = grouping.get("toolCallCase") or (bubble_data.get("toolFormerTool") if bubble_data else None)
                    tool_id = grouping.get("toolCallId") or (bubble_data.get("toolCallId") if bubble_data else None)
                    if tool_case or tool_id:
                        blocks.append(
                            ToolCallBlock(
                                tool_name=str(tool_case or "cursor_tool"),
                                tool_args={"toolCallId": tool_id, "status": grouping.get("toolFormerStatus")},
                            )
                        )

                    # Assistant suggested diffs
                    if bubble_data and bubble_data.get("assistantSuggestedDiffs"):
                        for diff in bubble_data["assistantSuggestedDiffs"]:
                            fpath = diff.get("relativeWorkspacePath") or "file"
                            blocks.append(
                                CodeDiffBlock(
                                    file_uri=fpath,
                                    diff=diff.get("diff", ""),
                                )
                            )

                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts or session.last_activity,
                        actor=Actor(role=role, model="Cursor AI"),
                        blocks=blocks,
                        harness_step_type="assistant_bubble",
                    )
                    steps.append(step)
                    step_idx += 1

            conn.close()
        except Exception:
            pass

        return steps
