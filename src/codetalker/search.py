from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from codetalker.adapter_base import BaseAdapter
from codetalker.registry import registry
from codetalker.utils.paths import normalize_working_directory, working_directories_match
from codetalker.utils.timestamps import timestamp_gte
from codetalker.schema import (
    ActorRole,
    NormalizedSession,
    NormalizedStep,
    ToolCallBlock,
    ToolResultBlock,
)

logger = logging.getLogger("codetalker.search")

LARGE_SESSION_STEP_THRESHOLD = 2000
FULL_SCAN_CHUNK_SIZE = 2000


def extract_block_text(block: Any) -> str:
    """Safely extract searchable text from any ContentBlock."""
    parts: list[str] = []
    for attr in (
        "text",
        "content",
        "tool_name",
        "diff",
        "file_uri",
        "name",
        "path",
        "event_name",
        "detail",
    ):
        val = getattr(block, attr, None)
        if val is not None:
            parts.append(str(val))
    tool_args = getattr(block, "tool_args", None)
    if tool_args is not None:
        try:
            parts.append(json.dumps(tool_args))
        except Exception:
            parts.append(str(tool_args))
    return " ".join(parts)


def normalize_queries(query: str, queries: list[str] | None = None) -> list[str]:
    terms: list[str] = []
    if query and query.strip():
        terms.append(query.strip())
    if queries:
        for q in queries:
            if q and q.strip() and q.strip().lower() not in {t.lower() for t in terms}:
                terms.append(q.strip())
    return terms


def _match_term(text: str, terms: list[str]) -> str | None:
    text_lower = text.lower()
    for term in terms:
        if term.lower() in text_lower:
            return term
    return None


def _step_text_preview(step: NormalizedStep, max_chars: int = 240) -> str:
    parts: list[str] = []
    for block in step.blocks:
        extracted = extract_block_text(block)
        if extracted:
            parts.append(extracted)
    joined = " ".join(parts).replace("\n", " ").strip()
    if len(joined) > max_chars:
        return joined[: max_chars - 3] + "..."
    return joined


def _tool_result_for_call(
    steps: list[NormalizedStep], step_idx: int, tool_call_id: str | None
) -> ToolResultBlock | None:
    if not tool_call_id:
        return None
    for step in steps[step_idx + 1 : step_idx + 6]:
        for block in step.blocks:
            if isinstance(block, ToolResultBlock) and block.tool_call_id == tool_call_id:
                return block
    return None


def _load_steps_for_search(
    adapter: BaseAdapter,
    session: NormalizedSession,
    search_scope: str,
) -> list[NormalizedStep]:
    if search_scope == "full":
        total = session.step_count or 0
        if total > LARGE_SESSION_STEP_THRESHOLD:
            all_steps: list[NormalizedStep] = []
            offset = 0
            while True:
                chunk = adapter.load_steps(
                    session=session,
                    offset=offset,
                    from_end=False,
                    limit=FULL_SCAN_CHUNK_SIZE,
                    include_raw_data=False,
                    include_thinking=True,
                )
                if not chunk:
                    break
                all_steps.extend(chunk)
                if len(chunk) < FULL_SCAN_CHUNK_SIZE:
                    break
                offset += len(chunk)
            return all_steps
        return adapter.load_steps(
            session=session,
            include_raw_data=False,
            include_thinking=True,
        )

    return adapter.load_steps(
        session=session,
        limit=50,
        from_end=True,
        include_raw_data=False,
        exclude_actor_roles=[ActorRole.SYSTEM],
    )


@dataclass
class SearchOptions:
    query: str = ""
    queries: list[str] | None = None
    harness: str | None = None
    since: str | None = None
    limit: int = 20
    max_sessions_to_search: int = 30
    max_hits_per_session: int = 1
    search_scope: str = "tail"
    match_tool_names: bool = True
    include_context_steps: int = 0
    root_path: str | None = None
    harnesses: list[str] | None = None
    working_directory: str | None = None


@dataclass
class SearchProgress:
    harnesses_searched: int = 0
    sessions_searched: int = 0
    sessions_skipped_since: int = 0


def search_sessions(
    options: SearchOptions,
    progress: SearchProgress | None = None,
) -> list[dict[str, Any]]:
    """Search sessions for query terms with configurable scope and caps."""
    terms = normalize_queries(options.query, options.queries)
    if not terms:
        return []

    if options.harnesses:
        harnesses = options.harnesses
    elif options.harness:
        harnesses = [options.harness]
    else:
        harnesses = registry.list_canonical_harnesses()

    unlimited_sessions = options.max_sessions_to_search <= 0
    unlimited_hits = options.max_hits_per_session <= 0
    matches: list[dict[str, Any]] = []

    for h in harnesses:
        if options.limit > 0 and len(matches) >= options.limit:
            break
        adapter = registry.get(h)
        if not adapter:
            continue
        if progress:
            progress.harnesses_searched += 1

        try:
            sessions = adapter.discover_sessions(root_path=options.root_path)
        except Exception as e:
            logger.warning("Error during search discovery for harness '%s': %s", h, e)
            continue

        sessions.sort(
            key=lambda s: s.last_activity or s.started_at or "", reverse=True
        )

        if options.working_directory:
            sessions = [
                s
                for s in sessions
                if working_directories_match(s.working_directory, options.working_directory)
            ]

        searched_count = 0
        session_hit_counts: dict[str, int] = {}

        for sess in sessions:
            if options.limit > 0 and len(matches) >= options.limit:
                break
            if options.since and not timestamp_gte(
                sess.last_activity or sess.started_at or "", options.since
            ):
                if progress:
                    progress.sessions_skipped_since += 1
                continue

            session_hits = session_hit_counts.get(sess.session_id, 0)
            if not unlimited_hits and session_hits >= options.max_hits_per_session:
                continue

            if sess.display_name:
                matched_term = _match_term(sess.display_name, terms)
                if matched_term and (
                    unlimited_hits or session_hits < options.max_hits_per_session
                ):
                    matches.append(
                        _build_hit(
                            harness=h,
                            session=sess,
                            match_type="title",
                            matched_term=matched_term,
                            preview=sess.display_name,
                            timestamp=sess.last_activity or sess.started_at,
                            step_index=None,
                        )
                    )
                    session_hit_counts[sess.session_id] = session_hit_counts.get(
                        sess.session_id, 0
                    ) + 1
                    session_hits += 1
                    if options.limit > 0 and len(matches) >= options.limit:
                        break
                    if not unlimited_hits and session_hits >= options.max_hits_per_session:
                        continue

            if not unlimited_sessions and searched_count >= options.max_sessions_to_search:
                continue

            searched_count += 1
            if progress:
                progress.sessions_searched += 1

            try:
                steps = _load_steps_for_search(adapter, sess, options.search_scope)
            except Exception as e:
                logger.debug(
                    "Error loading steps for session %s: %s", sess.session_id, e
                )
                continue

            for step_idx, step in enumerate(steps):
                session_hits = session_hit_counts.get(sess.session_id, 0)
                if not unlimited_hits and session_hits >= options.max_hits_per_session:
                    break

                for block in step.blocks:
                    matched_term: str | None = None
                    match_type = "content"
                    tool_name: str | None = None
                    tool_args_preview: str | None = None
                    tool_result_is_error: bool | None = None
                    tool_result_preview: str | None = None

                    if isinstance(block, ToolCallBlock) and options.match_tool_names:
                        tool_name = block.tool_name
                        matched_term = _match_term(block.tool_name, terms)
                        if not matched_term and block.tool_args:
                            matched_term = _match_term(
                                json.dumps(block.tool_args, default=str), terms
                            )
                        if matched_term:
                            match_type = "tool_call"
                            try:
                                tool_args_preview = json.dumps(block.tool_args)[:200]
                            except Exception:
                                tool_args_preview = str(block.tool_args)[:200]
                            result = _tool_result_for_call(steps, step_idx, block.tool_call_id)
                            if result:
                                tool_result_is_error = result.is_error
                                tool_result_preview = result.content[:300]

                    if not matched_term:
                        text_val = extract_block_text(block)
                        matched_term = _match_term(text_val, terms)
                        if matched_term and isinstance(block, ToolResultBlock):
                            match_type = "tool_result"
                            tool_result_is_error = block.is_error
                            tool_result_preview = block.content[:300]
                            tool_name = block.tool_name

                    if not matched_term:
                        continue

                    idx = extract_block_text(block).lower().find(matched_term.lower())
                    text_val = extract_block_text(block)
                    start = max(0, idx - 40)
                    end = min(len(text_val), idx + len(matched_term) + 60)
                    preview = text_val[start:end].replace("\n", " ")

                    hit = _build_hit(
                        harness=h,
                        session=sess,
                        match_type=match_type,
                        matched_term=matched_term,
                        preview=preview,
                        timestamp=step.timestamp,
                        step_index=step.step_index,
                        tool_name=tool_name,
                        tool_args_preview=tool_args_preview,
                        tool_result_is_error=tool_result_is_error,
                        tool_result_preview=tool_result_preview,
                    )

                    if options.include_context_steps > 0:
                        before_start = max(0, step_idx - options.include_context_steps)
                        after_end = min(
                            len(steps), step_idx + options.include_context_steps + 1
                        )
                        hit["context_before"] = [
                            _step_text_preview(steps[i])
                            for i in range(before_start, step_idx)
                        ]
                        hit["context_after"] = [
                            _step_text_preview(steps[i])
                            for i in range(step_idx + 1, after_end)
                        ]

                    matches.append(hit)
                    session_hit_counts[sess.session_id] = (
                        session_hit_counts.get(sess.session_id, 0) + 1
                    )
                    if options.limit > 0 and len(matches) >= options.limit:
                        break
                    if not unlimited_hits and session_hit_counts[sess.session_id] >= options.max_hits_per_session:
                        break

                if options.limit > 0 and len(matches) >= options.limit:
                    break

    return matches


def _build_hit(
    *,
    harness: str,
    session: NormalizedSession,
    match_type: str,
    matched_term: str,
    preview: str,
    timestamp: str | None,
    step_index: int | None,
    tool_name: str | None = None,
    tool_args_preview: str | None = None,
    tool_result_is_error: bool | None = None,
    tool_result_preview: str | None = None,
) -> dict[str, Any]:
    hit: dict[str, Any] = {
        "harness": harness,
        "session_id": session.session_id,
        "conversation_id": session.conversation_id,
        "display_name": session.display_name,
        "branch_label": session.branch_label,
        "match_type": match_type,
        "matched_term": matched_term,
        "preview": preview,
        "timestamp": timestamp,
        "step_index": step_index,
        "working_directory": session.working_directory,
        "normalized_working_directory": normalize_working_directory(session.working_directory),
    }
    if tool_name:
        hit["tool_name"] = tool_name
    if tool_args_preview:
        hit["tool_args_preview"] = tool_args_preview
    if tool_result_is_error is not None:
        hit["tool_result_is_error"] = tool_result_is_error
    if tool_result_preview:
        hit["tool_result_preview"] = tool_result_preview
    return hit
