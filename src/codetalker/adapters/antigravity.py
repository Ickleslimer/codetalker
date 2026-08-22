from __future__ import annotations

import glob
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from codetalker.adapter_base import BaseAdapter

logger = logging.getLogger("codetalker.adapters.antigravity")
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


def _clean_user_prompt(raw_text: str) -> str:
    """Extract clean user prompt from Antigravity raw USER_INPUT text."""
    if not raw_text:
        return ""
    # Extract <USER_REQUEST> ... </USER_REQUEST> if present
    match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Otherwise strip XML tags like <CONTEXT_SUMMARY>, <ADDITIONAL_METADATA>
    cleaned = re.sub(r"<CONTEXT_SUMMARY>.*?</CONTEXT_SUMMARY>", "", raw_text, flags=re.DOTALL)
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip() or raw_text.strip()


class AntigravityAdapter(BaseAdapter):
    """Adapter for Google Antigravity IDE and CLI agent conversation transcripts."""

    harness_name: str = "antigravity"

    def discover_sessions(
        self, root_path: str | None = None
    ) -> list[NormalizedSession]:
        sessions: list[NormalizedSession] = []

        if root_path:
            p = Path(root_path)
            if p.is_file():
                if p.name.endswith(".jsonl"):
                    sess = self._inspect_transcript_file(str(p))
                    if sess:
                        sessions.append(sess)
                return sessions
            elif p.is_dir():
                for tfile in p.glob("**/transcript.jsonl"):
                    sess = self._inspect_transcript_file(str(tfile))
                    if sess:
                        sessions.append(sess)
                return sessions

        # Default paths to check:
        # ~/.gemini/antigravity/brain/*/
        brain_dir = os.path.expanduser("~/.gemini/antigravity/brain")
        if os.path.isdir(brain_dir):
            for conv_dir in glob.glob(os.path.join(brain_dir, "*")):
                if os.path.isdir(conv_dir):
                    tpath = os.path.join(conv_dir, ".system_generated", "logs", "transcript.jsonl")
                    if os.path.isfile(tpath):
                        sess = self._inspect_transcript_file(tpath)
                        if sess:
                            sessions.append(sess)

        # Sort descending by last_activity
        sessions.sort(key=lambda s: s.last_activity or "", reverse=True)
        return sessions

    def _inspect_transcript_file(self, transcript_path: str) -> NormalizedSession | None:
        try:
            mtime = os.path.getmtime(transcript_path)
            last_activity = normalize_timestamp(mtime)

            # Path structure: brain/<conversation-id>/.system_generated/logs/transcript.jsonl
            conv_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(transcript_path))))
            session_id = os.path.basename(conv_dir)

            first_prompt: str | None = None
            started_at: str | None = None
            total_steps = 0
            user_turns = 0
            assistant_turns = 0

            with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    total_steps += 1
                    try:
                        data = json.loads(line)
                        ts = normalize_timestamp(data.get("created_at") or data.get("timestamp"))
                        if not started_at and ts:
                            started_at = ts
                        if ts:
                            last_activity = ts

                        stype = data.get("type")
                        if stype == "USER_INPUT":
                            user_turns += 1
                            if not first_prompt:
                                raw_c = data.get("content", "")
                                clean_c = _clean_user_prompt(raw_c)
                                if clean_c:
                                    first_prompt = clean_c[:60].strip().replace("\n", " ")
                        elif stype == "PLANNER_RESPONSE":
                            assistant_turns += 1
                    except Exception as e:
                        logger.debug(f"Failed to parse line {idx} in {transcript_path}: {e}")
                        continue

            if not started_at:
                started_at = last_activity

            display_name = first_prompt if first_prompt else f"Antigravity Session {session_id[:8]}"

            return NormalizedSession(
                session_id=session_id,
                harness="antigravity",
                display_name=display_name,
                conversation_id=session_id,
                branch_root_step_id=None,
                branch_label="Main Thread",
                started_at=started_at,
                last_activity=last_activity,
                model="Antigravity Agent",
                step_count=total_steps,
                user_turn_count=user_turns,
                assistant_turn_count=assistant_turns,
                source_path=transcript_path,
                source_format="jsonl",
                has_dag=False,
            )
        except Exception as e:
            logger.warning(f"Error inspecting Antigravity transcript '{transcript_path}': {e}")
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
        raw_steps = self._load_transcript_steps(session)

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

    def _load_transcript_steps(
        self, session: NormalizedSession
    ) -> list[NormalizedStep]:
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

                stype = data.get("type")
                source = data.get("source")
                ts = normalize_timestamp(data.get("created_at") or data.get("timestamp"))
                truncated_fields = set(data.get("truncated_fields") or [])

                blocks: list[ContentBlock] = []

                if stype == "USER_INPUT":
                    raw_content = data.get("content", "")
                    clean_content = _clean_user_prompt(raw_content)
                    blocks.append(
                        TextBlock(
                            text=clean_content or raw_content,
                            is_truncated="content" in truncated_fields,
                        )
                    )
                    step = NormalizedStep(
                        step_index=data.get("step_index", step_idx),
                        timestamp=ts,
                        actor=Actor(role=ActorRole.USER),
                        blocks=blocks,
                        status=data.get("status"),
                        raw_data=data,
                        harness_step_type=stype,
                    )
                    steps.append(step)
                    step_idx += 1

                elif stype == "PLANNER_RESPONSE":
                    # 1. Thinking block
                    thinking_str = data.get("thinking")
                    if thinking_str:
                        blocks.append(
                            ThinkingBlock(
                                text=thinking_str,
                                is_truncated="thinking" in truncated_fields,
                                has_signature=False,
                            )
                        )

                    # 2. Content block
                    content_str = data.get("content")
                    if content_str:
                        blocks.append(
                            TextBlock(
                                text=content_str,
                                is_truncated="content" in truncated_fields,
                            )
                        )

                    # 3. Tool calls
                    tool_calls = data.get("tool_calls") or []
                    for tc in tool_calls:
                        tc_name = tc.get("name") or "tool"
                        tc_args = tc.get("args") or {}
                        if isinstance(tc_args, str):
                            try:
                                tc_args = json.loads(tc_args)
                            except Exception:
                                tc_args = {"raw": tc_args}

                        blocks.append(
                            ToolCallBlock(
                                tool_name=tc_name,
                                tool_args=tc_args,
                                args_truncated="tool_calls" in truncated_fields,
                            )
                        )

                    if not blocks:
                        blocks.append(TextBlock(text=""))

                    # Check if subagent was invoked
                    spawned_id = None
                    for tc in tool_calls:
                        if tc.get("name") == "invoke_subagent":
                            # Will be resolved or matched
                            pass

                    step = NormalizedStep(
                        step_index=data.get("step_index", step_idx),
                        timestamp=ts,
                        actor=Actor(role=ActorRole.ASSISTANT, model="Antigravity Model"),
                        blocks=blocks,
                        spawned_session_id=spawned_id,
                        status=data.get("status"),
                        raw_data=data,
                        harness_step_type=stype,
                    )
                    steps.append(step)
                    step_idx += 1

                elif stype == "GENERIC":
                    # Tool execution output or system event
                    content_str = data.get("content") or ""
                    blocks.append(
                        ToolResultBlock(
                            content=content_str,
                            is_truncated="content" in truncated_fields,
                            is_error=(data.get("status") == "ERROR"),
                        )
                    )
                    step = NormalizedStep(
                        step_index=data.get("step_index", step_idx),
                        timestamp=ts,
                        actor=Actor(role=ActorRole.TOOL),
                        blocks=blocks,
                        status=data.get("status"),
                        raw_data=data,
                        harness_step_type="tool_result",
                    )
                    steps.append(step)
                    step_idx += 1

                elif stype == "CHECKPOINT":
                    checkpoint_text = data.get("content") or ""
                    blocks.append(
                        SystemEventBlock(
                            event_name="checkpoint",
                            detail=checkpoint_text,
                        )
                    )
                    step = NormalizedStep(
                        step_index=data.get("step_index", step_idx),
                        timestamp=ts,
                        actor=Actor(role=ActorRole.SYSTEM),
                        blocks=blocks,
                        status=data.get("status"),
                        raw_data=data,
                        harness_step_type=stype,
                    )
                    steps.append(step)
                    step_idx += 1

        return steps
