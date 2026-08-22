from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any, Sequence

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.chatgpt")
from codetalker.schema import (
    Actor,
    ActorRole,
    AttachmentBlock,
    BlockType,
    BranchInfo,
    CodeDiffBlock,
    ContentBlock,
    DiffStatus,
    NormalizedSession,
    NormalizedStep,
    SystemEventBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from codetalker.utils.timestamps import normalize_timestamp


class ChatGPTAdapter(BaseAdapter):
    """Adapter for OpenAI Codex CLI and ChatGPT desktop app / export transcripts."""

    harness_name: str = "chatgpt"

    # ─── Session Discovery ────────────────────────────────────────────────────

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        # If explicit path provided, check what format it is
        if root_path:
            p = Path(root_path)
            if p.is_file():
                if p.suffix.lower() == ".json":
                    sessions.extend(self._discover_json_export(str(p)))
                elif p.name.endswith(".jsonl"):
                    sessions.extend(self._discover_single_rollout(str(p)))
                return sessions
            elif p.is_dir():
                if (p / "session_index.jsonl").exists() or (p / "sessions").exists():
                    sessions.extend(self._discover_codex_rollouts(str(p)))
                if (p / "IndexedDB").exists() or any(p.glob("*.ldb")):
                    sessions.extend(self._discover_leveldb(str(p)))
                if not sessions:
                    # Check for any .json or .jsonl files in directory
                    for jf in p.glob("*.json"):
                        sessions.extend(self._discover_json_export(str(jf)))
                    for rlf in p.glob("**/rollout-*.jsonl"):
                        sessions.extend(self._discover_single_rollout(str(rlf)))
                return sessions

        # Default search locations:
        # 1. ~/.codex directory (Codex CLI sessions on disk)
        codex_home = os.path.expanduser("~/.codex")
        if os.path.isdir(codex_home):
            sessions.extend(self._discover_codex_rollouts(codex_home))

        # 2. Live LevelDB in %LOCALAPPDATA%\Packages\OpenAI.ChatGPT-Desktop_*\
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            pkg_pattern = os.path.join(
                localappdata,
                "Packages",
                "OpenAI.ChatGPT-Desktop_*",
                "LocalCache",
                "Roaming",
                "ChatGPT",
                "IndexedDB",
            )
            for idb_dir in glob.glob(pkg_pattern):
                if os.path.isdir(idb_dir):
                    sessions.extend(self._discover_leveldb(idb_dir))

        # 3. %APPDATA%\ChatGPT\IndexedDB
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_idb = os.path.join(appdata, "ChatGPT", "IndexedDB")
            if os.path.isdir(appdata_idb):
                sessions.extend(self._discover_leveldb(appdata_idb))

        # Sort sessions by last_activity descending
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    # ─── Codex CLI Rollouts Discovery ─────────────────────────────────────────

    def _discover_codex_rollouts(self, codex_dir: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        index_file = os.path.join(codex_dir, "session_index.jsonl")
        sessions_dir = os.path.join(codex_dir, "sessions")
        indexed_names: dict[str, str] = {}

        if os.path.isfile(index_file):
            try:
                with open(index_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            meta = json.loads(line)
                            session_id = meta.get("id")
                            if session_id:
                                thread_name = meta.get("thread_name")
                                if thread_name:
                                    indexed_names[session_id] = thread_name
                        except Exception:
                            continue
            except Exception:
                pass

        if os.path.isdir(sessions_dir):
            pattern = os.path.join(sessions_dir, "**", "rollout-*.jsonl")
            rollout_files = glob.glob(pattern, recursive=True)
            try:
                # Sort by real file mtime descending
                rollout_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            except Exception:
                pass

            for rollout_path in rollout_files:
                session = self._inspect_rollout_file(rollout_path, indexed_names)
                if session:
                    sessions.append(session)

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _find_rollout_file(self, session: NormalizedSession) -> str | None:
        """Resolve actual path on disk for a codex rollout session."""
        if os.path.isfile(session.source_path):
            return session.source_path

        codex_dir = os.path.expanduser("~/.codex/sessions")
        sid = session.conversation_id or session.session_id
        pattern = os.path.join(codex_dir, "**", f"rollout-*{sid}*.jsonl")
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
        return None

    def _discover_single_rollout(self, rollout_path: str) -> list[NormalizedSession]:
        session = self._inspect_rollout_file(rollout_path, {})
        return [session] if session else []

    def _inspect_rollout_file(
        self, rollout_path: str, indexed_names: dict[str, str]
    ) -> NormalizedSession | None:
        filename = os.path.basename(rollout_path)
        session_id = filename.replace("rollout-", "").replace(".jsonl", "")
        if len(session_id.split("-")) > 5:
            parts = session_id.split("-")
            uuid_part = "-".join(parts[3:]) if len(parts) >= 4 else session_id
        else:
            uuid_part = session_id

        display_name: str | None = indexed_names.get(uuid_part)
        mtime = os.path.getmtime(rollout_path)
        last_activity = normalize_timestamp(mtime)
        started_at: str | None = None
        model: str | None = None
        working_dir: str | None = None

        user_turn_count = 0
        assistant_turn_count = 0
        total_steps = 0

        try:
            with open(rollout_path, "r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f):
                    total_steps += 1
                    line = line.strip()
                    if not line:
                        continue
                    if idx >= 30:
                        # Fast metadata peek: header lines contain metadata, break to avoid reading 10k+ lines
                        break
                    try:
                        entry = json.loads(line)
                        ts = normalize_timestamp(entry.get("timestamp"))
                        if not started_at and ts:
                            started_at = ts

                        etype = entry.get("type")
                        payload = entry.get("payload") or {}

                        if etype == "turn_context":
                            if not working_dir:
                                working_dir = payload.get("cwd")
                            if not model:
                                model = payload.get("model")

                        elif etype == "session_meta":
                            sid = payload.get("session_id") or payload.get("id")
                            if sid:
                                session_id = sid
                            title = payload.get("title") or payload.get("thread_name")
                            if title and not display_name:
                                display_name = title

                        elif etype == "event_msg":
                            msg_type = payload.get("type")
                            if msg_type == "user_message":
                                user_turn_count += 1
                                if not display_name:
                                    msg_text = payload.get("message", "")
                                    display_name = msg_text[:40].strip().replace("\n", " ")
                            elif msg_type == "agent_message":
                                assistant_turn_count += 1

                        elif etype == "response_item":
                            role = payload.get("role")
                            if role == "user":
                                user_turn_count += 1
                            elif role == "assistant":
                                assistant_turn_count += 1
                    except Exception as e:
                        logger.debug(f"Error parsing rollout line {idx} in {rollout_path}: {e}")
                        continue
        except Exception as e:
            logger.warning(f"Error reading rollout file '{rollout_path}': {e}")
            return None

        if not display_name:
            display_name = f"Codex Session {session_id[:8]}"

        return NormalizedSession(
            session_id=session_id,
            harness="chatgpt",
            display_name=display_name,
            conversation_id=uuid_part,
            branch_root_step_id=None,
            branch_label="Main Thread",
            started_at=started_at,
            last_activity=last_activity,
            working_directory=working_dir,
            model=model,
            step_count=total_steps,
            user_turn_count=user_turn_count,
            assistant_turn_count=assistant_turn_count,
            source_path=rollout_path,
            source_format="codex_rollout",
            has_dag=False,
        )

    # ─── JSON Export DAG Discovery ───────────────────────────────────────────

    def _discover_json_export(self, json_path: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        try:
            with open(json_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            return sessions

        # If data is a single conversation dict
        if isinstance(data, dict):
            convs = [data]
        elif isinstance(data, list):
            convs = [c for c in data if isinstance(c, dict)]
        else:
            return sessions

        for conv in convs:
            if "mapping" in conv:
                sessions.extend(self._extract_dag_branches(conv, json_path, "export_json"))

        return sessions

    # ─── LevelDB Discovery ───────────────────────────────────────────────────

    def _discover_leveldb(self, leveldb_dir: str) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []
        try:
            from ccl_chromium_reader import ccl_chromium_indexeddb

            idb = ccl_chromium_indexeddb.WrappedIndexDB(leveldb_dir)
            for db_id in idb.database_ids:
                db = idb.database_ids[db_id] if hasattr(idb, "database_ids") else None
                # Iterate object stores
                if hasattr(idb, "database_count"):
                    for db_name in idb.database_ids:
                        db = idb[db_name] if hasattr(idb, "__getitem__") else None
                        if not db:
                            continue
                        for os_name in db.object_store_names:
                            store = db.get_object_store_by_name(os_name)
                            for record in store.iterate_records():
                                val = getattr(record, "value", None)
                                if isinstance(val, dict) and "mapping" in val:
                                    sessions.extend(
                                        self._extract_dag_branches(
                                            val, leveldb_dir, "leveldb"
                                        )
                                    )
        except Exception:
            # LevelDB might be locked or inaccessible; fail gracefully
            pass

        return sessions

    # ─── DAG Branch Extraction (ChatGPT Multi-Thread) ─────────────────────────

    def _extract_dag_branches(
        self, conv: dict[str, Any], source_path: str, source_format: str
    ) -> list[NormalizedSession]:
        conv_id = conv.get("id") or "conv_unknown"
        title = conv.get("title") or f"ChatGPT Conversation {conv_id[:8]}"
        created_at = normalize_timestamp(conv.get("create_time"))
        updated_at = normalize_timestamp(conv.get("update_time"))
        current_node = conv.get("current_node")
        mapping = conv.get("mapping") or {}

        if not mapping:
            return []

        # Find all leaf nodes (nodes with 0 children)
        leaf_nodes: list[str] = []
        for node_id, node in mapping.items():
            children = node.get("children") or []
            if not children:
                leaf_nodes.append(node_id)

        if not leaf_nodes and current_node:
            leaf_nodes = [current_node]

        # Calculate primary trunk path from current_node backwards to root
        trunk_path_set: set[str] = set()
        curr = current_node
        while curr and curr in mapping:
            trunk_path_set.add(curr)
            curr = mapping[curr].get("parent")

        sessions: list[NormalizedSession] = []

        for idx, leaf_id in enumerate(leaf_nodes):
            # Trace path from leaf to root
            path: list[str] = []
            curr = leaf_id
            divergence_point: str | None = None

            while curr and curr in mapping:
                path.append(curr)
                parent = mapping[curr].get("parent")
                if curr not in trunk_path_set and parent in trunk_path_set:
                    divergence_point = parent
                curr = parent

            path.reverse()  # Chronological root -> leaf
            is_trunk = (leaf_id == current_node)

            if is_trunk:
                session_id = conv_id
                display_name = title
                branch_root_id = None
                branch_label = "Main Thread"
            else:
                session_id = f"{conv_id}__branch_{leaf_id}"
                display_name = f"{title} · Branch {idx + 1}"
                branch_root_id = divergence_point
                branch_label = f"Fork at step {divergence_point[:8]}" if divergence_point else f"Branch {idx + 1}"

            # Calculate counts along this branch
            user_turns = 0
            assistant_turns = 0
            model_slug: str | None = None

            for nid in path:
                node = mapping.get(nid) or {}
                msg = node.get("message")
                if msg:
                    role = msg.get("author", {}).get("role")
                    if role == "user":
                        user_turns += 1
                    elif role == "assistant":
                        assistant_turns += 1
                        if not model_slug:
                            model_slug = msg.get("metadata", {}).get("model_slug")

            sess = NormalizedSession(
                session_id=session_id,
                harness="chatgpt",
                display_name=display_name,
                conversation_id=conv_id,
                branch_root_step_id=branch_root_id,
                branch_label=branch_label,
                started_at=created_at,
                last_activity=updated_at,
                model=model_slug,
                step_count=len(path),
                user_turn_count=user_turns,
                assistant_turn_count=assistant_turns,
                source_path=source_path,
                source_format=source_format,
                active_node_id=leaf_id,
                has_dag=True,
            )
            sessions.append(sess)

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
        include_thinking: bool = True,
        include_raw_data: bool = True,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        if session.source_format == "codex_rollout":
            raw_steps = self._load_codex_rollout_steps(session)
        elif session.source_format in ("export_json", "dag_json"):
            raw_steps = self._load_dag_json_steps(session)
        elif session.source_format == "leveldb":
            raw_steps = self._load_leveldb_steps(session)
        else:
            raw_steps = []

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

    def _load_codex_rollout_steps(
        self, session: NormalizedSession
    ) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        path = self._find_rollout_file(session)
        if not path or not os.path.isfile(path):
            return steps

        step_idx = 0
        current_model = session.model

        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue

                etype = entry.get("type")
                payload = entry.get("payload") or {}
                ts = normalize_timestamp(entry.get("timestamp"))

                if etype == "turn_context":
                    current_model = payload.get("model") or current_model
                    # Optionally record turn context as system event
                    blocks: list[ContentBlock] = [
                        SystemEventBlock(
                            event_name="turn_context",
                            detail=json.dumps(
                                {
                                    "cwd": payload.get("cwd"),
                                    "model": payload.get("model"),
                                    "approval_policy": payload.get("approval_policy"),
                                }
                            ),
                        )
                    ]
                    step = NormalizedStep(
                        step_index=step_idx,
                        timestamp=ts,
                        actor=Actor(role=ActorRole.SYSTEM, model=current_model),
                        blocks=blocks,
                        raw_data=entry,
                        harness_step_type=etype,
                    )
                    steps.append(step)
                    step_idx += 1

                elif etype == "event_msg":
                    msg_type = payload.get("type")
                    if msg_type == "user_message":
                        msg_text = payload.get("message") or ""
                        blocks = [TextBlock(text=msg_text)]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.USER),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="user_message",
                        )
                        steps.append(step)
                        step_idx += 1

                    elif msg_type == "agent_message":
                        msg_text = payload.get("message") or ""
                        blocks = [TextBlock(text=msg_text)]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.ASSISTANT, model=current_model),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="agent_message",
                        )
                        steps.append(step)
                        step_idx += 1

                    elif msg_type in ("custom_tool_call", "tool_search_call", "web_search_call"):
                        tool_name = payload.get("name") or msg_type
                        tool_args = payload.get("args") or payload.get("input") or {}
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {"raw": tool_args}
                        blocks = [
                            ToolCallBlock(
                                tool_call_id=payload.get("call_id"),
                                tool_name=tool_name,
                                tool_args=tool_args,
                            )
                        ]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.ASSISTANT, model=current_model),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type=msg_type,
                        )
                        steps.append(step)
                        step_idx += 1

                    elif msg_type in ("custom_tool_call_output", "tool_search_output", "web_search_end"):
                        out_content = payload.get("output") or payload.get("result") or ""
                        if not isinstance(out_content, str):
                            out_content = json.dumps(out_content)
                        blocks = [
                            ToolResultBlock(
                                tool_call_id=payload.get("call_id"),
                                tool_name=payload.get("name"),
                                content=out_content,
                                is_error=bool(payload.get("is_error")),
                            )
                        ]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.TOOL),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type=msg_type,
                        )
                        steps.append(step)
                        step_idx += 1

                elif etype == "response_item":
                    rtype = payload.get("type")
                    if rtype == "reasoning":
                        # Encrypted or plain reasoning content
                        summary = payload.get("summary") or []
                        content = payload.get("content") or ""
                        if content:
                            reasoning_text = content
                        elif isinstance(summary, list):
                            content_parts = []
                            for s in summary:
                                if isinstance(s, dict):
                                    content_parts.append(s.get("text") or s.get("content") or json.dumps(s))
                                elif isinstance(s, str):
                                    content_parts.append(s)
                                else:
                                    content_parts.append(str(s))
                            reasoning_text = " ".join(content_parts)
                        else:
                            reasoning_text = str(summary) if summary else ""

                        if not reasoning_text and payload.get("encrypted_content"):
                            reasoning_text = "[Encrypted Reasoning Content]"

                        blocks = [ThinkingBlock(text=reasoning_text or "[Reasoning]")]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.ASSISTANT, model=current_model),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="reasoning",
                        )
                        steps.append(step)
                        step_idx += 1

                    elif rtype == "function_call":
                        fn_name = payload.get("name") or "function"
                        fn_args = payload.get("arguments") or {}
                        if isinstance(fn_args, str):
                            try:
                                fn_args = json.loads(fn_args)
                            except Exception:
                                fn_args = {"raw": fn_args}
                        blocks = [
                            ToolCallBlock(
                                tool_call_id=payload.get("call_id"),
                                tool_name=fn_name,
                                tool_args=fn_args,
                            )
                        ]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.ASSISTANT, model=current_model),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="function_call",
                        )
                        steps.append(step)
                        step_idx += 1

                    elif rtype == "function_call_output":
                        out = payload.get("output") or ""
                        if not isinstance(out, str):
                            out = json.dumps(out)
                        blocks = [
                            ToolResultBlock(
                                tool_call_id=payload.get("call_id"),
                                content=out,
                            )
                        ]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=ActorRole.TOOL),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="function_call_output",
                        )
                        steps.append(step)
                        step_idx += 1

                    elif rtype == "message":
                        role_str = payload.get("role", "assistant")
                        role = ActorRole.USER if role_str == "user" else ActorRole.ASSISTANT
                        content_list = payload.get("content") or []
                        text_parts: list[str] = []
                        for part in content_list:
                            if isinstance(part, dict) and "text" in part:
                                text_parts.append(part["text"])
                            elif isinstance(part, str):
                                text_parts.append(part)
                        blocks = [TextBlock(text="\n".join(text_parts))]
                        step = NormalizedStep(
                            step_index=step_idx,
                            timestamp=ts,
                            actor=Actor(role=role, model=current_model),
                            blocks=blocks,
                            raw_data=entry,
                            harness_step_type="message",
                        )
                        steps.append(step)
                        step_idx += 1

        return steps

    # ─── DAG JSON Step Loader ─────────────────────────────────────────────────

    def _load_dag_json_steps(
        self, session: NormalizedSession
    ) -> list[NormalizedStep]:
        steps: list[NormalizedStep] = []
        if not os.path.isfile(session.source_path):
            return steps

        try:
            with open(session.source_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
        except Exception:
            return steps

        conv = None
        if isinstance(data, dict):
            if data.get("id") == session.conversation_id:
                conv = data
        elif isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and c.get("id") == session.conversation_id:
                    conv = c
                    break

        if not conv or "mapping" not in conv:
            return steps

        mapping = conv["mapping"]
        leaf_id = session.active_node_id or conv.get("current_node")

        # Trace path from leaf to root
        path: list[str] = []
        curr = leaf_id
        while curr and curr in mapping:
            path.append(curr)
            curr = mapping[curr].get("parent")
        path.reverse()

        step_idx = 0
        for nid in path:
            node = mapping.get(nid) or {}
            msg = node.get("message")
            if not msg:
                continue

            author = msg.get("author") or {}
            role_str = author.get("role")
            if role_str == "user":
                role = ActorRole.USER
            elif role_str == "assistant":
                role = ActorRole.ASSISTANT
            elif role_str == "tool":
                role = ActorRole.TOOL
            else:
                role = ActorRole.SYSTEM

            meta = msg.get("metadata") or {}
            model_name = meta.get("model_slug")
            ts = normalize_timestamp(msg.get("create_time"))

            blocks: list[ContentBlock] = []

            # 1. Thought / reasoning
            thought = meta.get("thought")
            if thought:
                blocks.append(ThinkingBlock(text=thought))

            # 2. Content
            content_dict = msg.get("content") or {}
            ctype = content_dict.get("content_type")
            parts = content_dict.get("parts") or []

            if ctype == "text":
                text_content = "\n".join(p for p in parts if isinstance(p, str))
                if text_content:
                    blocks.append(TextBlock(text=text_content))

            elif ctype == "execution_output":
                text_content = "\n".join(p for p in parts if isinstance(p, str))
                blocks.append(
                    ToolResultBlock(
                        tool_name="code_interpreter",
                        content=text_content,
                    )
                )

            elif ctype in ("tether_browsing_display", "tether_quote"):
                text_content = "\n".join(p for p in parts if isinstance(p, str))
                blocks.append(
                    ToolResultBlock(
                        tool_name="web_browsing",
                        content=text_content,
                    )
                )

            elif ctype == "multimodal_text":
                for part in parts:
                    if isinstance(part, str):
                        blocks.append(TextBlock(text=part))
                    elif isinstance(part, dict):
                        if part.get("content_type") == "image_asset_pointer":
                            blocks.append(
                                AttachmentBlock(
                                    attachment_type="image",
                                    url=part.get("asset_pointer"),
                                )
                            )

            if not blocks:
                blocks.append(TextBlock(text=""))

            branch_info = BranchInfo(
                step_id=node.get("id", nid),
                parent_step_id=node.get("parent"),
                children_step_ids=node.get("children") or [],
                is_on_active_path=(leaf_id == conv.get("current_node")),
            )

            step = NormalizedStep(
                step_index=step_idx,
                timestamp=ts,
                actor=Actor(
                    role=role,
                    agent_id=author.get("name"),
                    model=model_name,
                ),
                blocks=blocks,
                branch=branch_info,
                status=msg.get("status"),
                raw_data=node,
                harness_step_type=ctype or role_str,
                finish_reason=meta.get("finish_details", {}).get("type"),
            )
            steps.append(step)
            step_idx += 1

        return steps

    # ─── LevelDB Step Loader ─────────────────────────────────────────────────

    def _load_leveldb_steps(
        self, session: NormalizedSession
    ) -> list[NormalizedStep]:
        # LevelDB contains the same mapping dict structure in IndexedDB record values
        try:
            from ccl_chromium_reader import ccl_chromium_indexeddb

            idb = ccl_chromium_indexeddb.WrappedIndexDB(session.source_path)
            for db_name in getattr(idb, "database_ids", []):
                db = idb[db_name] if hasattr(idb, "__getitem__") else None
                if not db:
                    continue
                for os_name in db.object_store_names:
                    store = db.get_object_store_by_name(os_name)
                    for record in store.iterate_records():
                        val = getattr(record, "value", None)
                        if isinstance(val, dict) and val.get("id") == session.conversation_id:
                            # Re-use DAG loader logic in memory
                            dummy_path = "memory://conv.json"
                            temp_conv = val
                            # Load DAG steps directly from val mapping
                            mapping = temp_conv.get("mapping") or {}
                            leaf_id = session.active_node_id or temp_conv.get("current_node")
                            path: list[str] = []
                            curr = leaf_id
                            while curr and curr in mapping:
                                path.append(curr)
                                curr = mapping[curr].get("parent")
                            path.reverse()

                            steps: list[NormalizedStep] = []
                            step_idx = 0
                            for nid in path:
                                node = mapping.get(nid) or {}
                                msg = node.get("message")
                                if not msg:
                                    continue
                                author = msg.get("author") or {}
                                role_str = author.get("role")
                                role = ActorRole.USER if role_str == "user" else ActorRole.ASSISTANT
                                meta = msg.get("metadata") or {}
                                ts = normalize_timestamp(msg.get("create_time"))

                                blocks: list[ContentBlock] = []
                                thought = meta.get("thought")
                                if thought:
                                    blocks.append(ThinkingBlock(text=thought))

                                content_dict = msg.get("content") or {}
                                parts = content_dict.get("parts") or []
                                text_content = "\n".join(p for p in parts if isinstance(p, str))
                                if text_content:
                                    blocks.append(TextBlock(text=text_content))
                                if not blocks:
                                    blocks.append(TextBlock(text=""))

                                step = NormalizedStep(
                                    step_index=step_idx,
                                    timestamp=ts,
                                    actor=Actor(role=role, model=meta.get("model_slug")),
                                    blocks=blocks,
                                    branch=BranchInfo(step_id=nid, parent_step_id=node.get("parent")),
                                    raw_data=node,
                                    harness_step_type=content_dict.get("content_type") or role_str,
                                )
                                steps.append(step)
                                step_idx += 1
                            return steps
        except Exception:
            pass

        return []
