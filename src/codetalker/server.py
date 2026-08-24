from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import click
from mcp.server.mcpserver import MCPServer

# Ensure adapters are registered
import codetalker.adapters  # noqa: F401
from codetalker.registry import registry
from codetalker.schema import ActorRole, BlockType, NormalizedSession, NormalizedStep

logger = logging.getLogger("codetalker.server")

server = MCPServer("codetalker")


def _get_adapter(harness: str | None = None) -> Any:
    if harness:
        adapter = registry.get(harness)
        if adapter:
            return adapter
    canonical = registry.list_canonical_harnesses()
    if harness:
        raise ValueError(
            f"Unknown harness '{harness}'. Registered harnesses: {registry.list_harnesses()}"
        )
    if canonical:
        return registry.get(canonical[0])
    raise ValueError("No adapters registered")


def _extract_block_text(b: Any) -> str:
    """Safely extract all searchable/filterable text from any ContentBlock without NoneType concatenation."""
    parts: list[str] = []
    # Text block & Thinking block
    t = getattr(b, "text", None)
    if t is not None:
        parts.append(str(t))
    # Content block / Tool result
    c = getattr(b, "content", None)
    if c is not None:
        parts.append(str(c))
    # Tool call name and args
    tn = getattr(b, "tool_name", None)
    if tn is not None:
        parts.append(str(tn))
    ta = getattr(b, "tool_args", None)
    if ta is not None:
        try:
            parts.append(json.dumps(ta))
        except Exception:
            parts.append(str(ta))
    # Code diffs
    d = getattr(b, "diff", None)
    if d is not None:
        parts.append(str(d))
    uri = getattr(b, "file_uri", None)
    if uri is not None:
        parts.append(str(uri))
    # Attachments
    nm = getattr(b, "name", None)
    if nm is not None:
        parts.append(str(nm))
    p = getattr(b, "path", None)
    if p is not None:
        parts.append(str(p))
    # System events
    ev = getattr(b, "event_name", None)
    if ev is not None:
        parts.append(str(ev))
    dt = getattr(b, "detail", None)
    if dt is not None:
        parts.append(str(dt))
    return " ".join(parts)


def _search_candidate_sessions(
    clean_id_lower: str, candidate_adapters: list[Any], root_path: str | None = None
) -> tuple[Any, NormalizedSession] | None:
    """Search for a session matching clean_id_lower across candidate adapters."""
    # 1. Exact match on session_id or conversation_id
    for ad in candidate_adapters:
        try:
            sessions = ad.discover_sessions(root_path=root_path)
            for s in sessions:
                if s.session_id.lower() == clean_id_lower:
                    return ad, s
                if (
                    s.conversation_id
                    and s.conversation_id.lower() == clean_id_lower
                    and s.branch_root_step_id is None
                ):
                    return ad, s
        except Exception as e:
            logger.debug(f"Error discovering sessions for {ad.harness_name}: {e}")
            continue

    # 2. Prefix match (if >= 6 chars)
    if len(clean_id_lower) >= 6:
        for ad in candidate_adapters:
            try:
                sessions = ad.discover_sessions(root_path=root_path)
                for s in sessions:
                    if s.session_id.lower().startswith(clean_id_lower):
                        return ad, s
                    if s.conversation_id and s.conversation_id.lower().startswith(
                        clean_id_lower
                    ):
                        return ad, s
            except Exception:
                continue

    # 3. Substring match on session_id or display_name
    for ad in candidate_adapters:
        try:
            sessions = ad.discover_sessions(root_path=root_path)
            for s in sessions:
                if clean_id_lower in s.session_id.lower():
                    return ad, s
                if s.display_name and clean_id_lower in s.display_name.lower():
                    return ad, s
        except Exception:
            continue

    # 4. Fallback search inside recent session steps for keyword/title
    for ad in candidate_adapters:
        try:
            sessions = ad.discover_sessions(root_path=root_path)
            for s in sessions[:20]:
                try:
                    steps = ad.load_steps(session=s, limit=10)
                    for st in steps:
                        for b in st.blocks:
                            txt = _extract_block_text(b)
                            if clean_id_lower in txt.lower():
                                return ad, s
                except Exception:
                    continue
        except Exception:
            continue

    return None


def _find_session(
    session_id: str, harness: str | None = None, root_path: str | None = None
) -> tuple[Any, NormalizedSession]:
    """Robustly find a session by exact ID, prefix, URI, or title across harnesses with graceful root_path fallback."""
    clean_id = session_id.strip().strip("'\"")
    if clean_id.startswith("conversation://"):
        clean_id = clean_id[len("conversation://") :]
    if clean_id.startswith("file://"):
        clean_id = clean_id[len("file://") :]
    clean_id = clean_id.replace("\\", "/").rstrip("/")
    if "/" in clean_id:
        parts = [
            p
            for p in clean_id.split("/")
            if p
            and p
            not in (
                "transcript.jsonl",
                "logs",
                ".system_generated",
                "state.vscdb",
                "drafts.sqlite",
            )
        ]
        if parts:
            clean_id = parts[-1]

    clean_id_lower = clean_id.lower()

    # Determine candidate adapters
    candidate_adapters: list[Any] = []
    if harness:
        adapter = registry.get(harness)
        if adapter:
            candidate_adapters.append(adapter)
    if not candidate_adapters:
        for h in registry.list_canonical_harnesses():
            ad = registry.get(h)
            if ad and ad not in candidate_adapters:
                candidate_adapters.append(ad)

    # 1. Search with root_path if provided
    res = _search_candidate_sessions(clean_id_lower, candidate_adapters, root_path=root_path)
    if res:
        return res

    # 2. If root_path was provided and search failed, fallback to global discovery
    if root_path is not None:
        res = _search_candidate_sessions(clean_id_lower, candidate_adapters, root_path=None)
        if res:
            return res

    h_msg = f" for harness '{harness}'" if harness else " across all harnesses"
    raise ValueError(f"Session '{session_id}' not found{h_msg}.")


# ─── Tools ────────────────────────────────────────────────────────────────────


@server.tool(
    name="codetalk_list",
    description="List conversation sessions and branch threads across one or all coding harnesses.",
)
def codetalk_list(
    harness: str | None = None,
    conversation_id: str | None = None,
    since: str | None = None,
    limit: int = 50,
    root_path: str | None = None,
) -> str:
    """List session shells (metadata only, fast)."""
    if harness:
        harnesses = [harness]
    else:
        harnesses = registry.list_canonical_harnesses()

    all_sessions: list[NormalizedSession] = []
    seen_keys: set[tuple[str, str]] = set()

    for h in harnesses:
        adapter = registry.get(h)
        if adapter:
            try:
                discovered = adapter.discover_sessions(root_path=root_path)
                for s in discovered:
                    # Deduplicate by (canonical_harness, session_id)
                    canonical = registry.get_canonical_name(s.harness)
                    dedup_key = (canonical, s.session_id)
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_sessions.append(s)
            except Exception as e:
                logger.warning(
                    f"Failed discovery for harness '{h}' (root_path={root_path}): {e}",
                    exc_info=True,
                )
                continue

    # Filter by conversation_id if specified
    if conversation_id:
        all_sessions = [
            s
            for s in all_sessions
            if s.conversation_id == conversation_id or s.session_id == conversation_id
        ]

    # Filter by timestamp
    if since:
        all_sessions = [
            s for s in all_sessions if (s.last_activity or s.started_at or "") >= since
        ]

    # Sort descending by last_activity
    all_sessions.sort(key=lambda s: s.last_activity or s.started_at or "", reverse=True)

    if limit > 0:
        all_sessions = all_sessions[:limit]

    # Return clean JSON summary
    summaries = [
        {
            "session_id": s.session_id,
            "harness": s.harness,
            "display_name": s.display_name,
            "conversation_id": s.conversation_id,
            "branch_label": s.branch_label,
            "branch_root_step_id": s.branch_root_step_id,
            "started_at": s.started_at,
            "last_activity": s.last_activity,
            "working_directory": s.working_directory,
            "model": s.model,
            "step_count": s.step_count,
            "user_turn_count": s.user_turn_count,
            "assistant_turn_count": s.assistant_turn_count,
            "has_dag": s.has_dag,
            "source_format": s.source_format,
        }
        for s in all_sessions
    ]

    return json.dumps(
        {
            "count": len(summaries),
            "harnesses": registry.list_canonical_harnesses(),
            "aliases": registry.list_aliases(),
            "sessions": summaries,
        },
        indent=2,
    )


@server.tool(
    name="codetalk_read",
    description="Read and normalize transcript steps for a specific session thread. Harness is optional.",
)
def codetalk_read(
    session_id: str,
    harness: str | None = None,
    since: str | None = None,
    until: str | None = None,
    since_last_user_input: bool = False,
    include_thinking: bool = True,
    limit: int = 30,
    root_path: str | None = None,
) -> str:
    """Read normalized steps for a given session."""
    adapter, session = _find_session(session_id, harness, root_path=root_path)
    steps = adapter.load_steps(
        session=session,
        since=since,
        until=until,
        since_last_user_input=since_last_user_input,
        include_thinking=include_thinking,
        limit=limit if limit and limit > 0 else 30,
    )

    return json.dumps(
        {
            "session": {
                "session_id": session.session_id,
                "harness": session.harness,
                "display_name": session.display_name,
                "conversation_id": session.conversation_id,
                "branch_label": session.branch_label,
                "started_at": session.started_at,
                "last_activity": session.last_activity,
                "working_directory": session.working_directory,
                "model": session.model,
                "total_steps_in_thread": session.step_count,
                "returned_step_count": len(steps),
            },
            "steps": [s.model_dump(mode="json", exclude_none=True) for s in steps],
        },
        indent=2,
    )


@server.tool(
    name="codetalk_filter",
    description="Filter steps in a session by keywords, step types, or actor roles.",
)
def codetalk_filter(
    session_id: str,
    harness: str | None = None,
    keywords: list[str] | None = None,
    step_types: list[str] | None = None,
    actor_roles: list[str] | None = None,
    since_last_user_input: bool = False,
    include_thinking: bool = True,
    limit: int | None = None,
    root_path: str | None = None,
) -> str:
    """Filter steps in a session."""
    adapter, session = _find_session(session_id, harness, root_path=root_path)

    parsed_step_types: list[BlockType] | None = None
    if step_types:
        parsed_step_types = [
            BlockType(st.lower())
            for st in step_types
            if st.lower() in BlockType.__members__.values()
        ]

    parsed_actor_roles: list[ActorRole] | None = None
    if actor_roles:
        parsed_actor_roles = [
            ActorRole(ar.lower())
            for ar in actor_roles
            if ar.lower() in ActorRole.__members__.values()
        ]

    steps = adapter.load_steps(
        session=session,
        since_last_user_input=since_last_user_input,
        include_step_types=parsed_step_types,
        include_actor_roles=parsed_actor_roles,
        include_thinking=include_thinking,
    )

    # Apply keyword filtering if provided
    if keywords:
        kw_lower = [k.lower() for k in keywords]
        filtered_by_kw: list[NormalizedStep] = []
        for step in steps:
            step_text = ""
            for b in step.blocks:
                extracted = _extract_block_text(b)
                if extracted:
                    step_text += " " + extracted

            if any(k in step_text.lower() for k in kw_lower):
                filtered_by_kw.append(step)
        steps = filtered_by_kw

    if limit is not None and limit > 0:
        steps = steps[:limit]

    return json.dumps(
        {
            "session_id": session.session_id,
            "harness": session.harness,
            "returned_step_count": len(steps),
            "steps": [s.model_dump(mode="json", exclude_none=True) for s in steps],
        },
        indent=2,
    )


@server.tool(
    name="codetalk_search",
    description="Search across all sessions and transcripts for a query string.",
)
def codetalk_search(
    query: str,
    harness: str | None = None,
    since: str | None = None,
    limit: int = 20,
    max_sessions_to_search: int = 30,
    root_path: str | None = None,
) -> str:
    """Search for query in session transcripts efficiently."""
    if harness:
        harnesses = [harness]
    else:
        harnesses = registry.list_canonical_harnesses()

    query_lower = query.lower()
    matches: list[dict[str, Any]] = []

    for h in harnesses:
        if len(matches) >= limit:
            break
        adapter = registry.get(h)
        if not adapter:
            continue
        try:
            sessions = adapter.discover_sessions(root_path=root_path)
        except Exception as e:
            logger.warning(f"Error during search discovery for harness '{h}': {e}")
            continue

        # Sort sessions by recency
        sessions.sort(
            key=lambda s: s.last_activity or s.started_at or "", reverse=True
        )

        searched_count = 0
        for sess in sessions:
            if len(matches) >= limit:
                break
            if since and (sess.last_activity or sess.started_at or "") < since:
                continue

            # 1. Fast match on display_name / title
            if sess.display_name and query_lower in sess.display_name.lower():
                matches.append(
                    {
                        "harness": h,
                        "session_id": sess.session_id,
                        "display_name": sess.display_name,
                        "match_type": "title",
                        "preview": sess.display_name,
                        "timestamp": sess.last_activity or sess.started_at,
                    }
                )
                if len(matches) >= limit:
                    break

            # 2. Search inside content for recent sessions
            if searched_count < max_sessions_to_search:
                searched_count += 1
                try:
                    steps = adapter.load_steps(session=sess, limit=50)
                    for s in steps:
                        for b in s.blocks:
                            text_val = _extract_block_text(b)
                            if query_lower in text_val.lower():
                                idx = text_val.lower().find(query_lower)
                                start = max(0, idx - 40)
                                end = min(len(text_val), idx + len(query) + 60)
                                preview = text_val[start:end].replace("\n", " ")

                                matches.append(
                                    {
                                        "harness": h,
                                        "session_id": sess.session_id,
                                        "display_name": sess.display_name,
                                        "match_type": "content",
                                        "preview": preview,
                                        "timestamp": s.timestamp,
                                    }
                                )
                                break
                        if len(matches) >= limit:
                            break
                except Exception as e:
                    logger.debug(
                        f"Error reading steps for session {sess.session_id}: {e}"
                    )
                    continue

    return json.dumps(
        {
            "query": query,
            "match_count": len(matches),
            "results": matches,
        },
        indent=2,
    )


@server.tool(
    name="codetalk_info",
    description="Get metadata for a specific session without loading step bodies.",
)
def codetalk_info(
    session_id: str, harness: str | None = None, root_path: str | None = None
) -> str:
    """Get metadata for a session."""
    _, session = _find_session(session_id, harness, root_path=root_path)
    return json.dumps(session.model_dump(mode="json", exclude={"steps"}), indent=2)


@server.tool(
    name="codetalk_branches",
    description="Get the full DAG branch, fork points, and subagent hierarchy for a conversation.",
)
def codetalk_branches(
    conversation_id: str,
    harness: str | None = None,
    root_path: str | None = None,
) -> str:
    """Get the full DAG branch hierarchy for a conversation."""
    adapter, _ = _find_session(conversation_id, harness, root_path=root_path)
    tree = adapter.get_branch_tree(
        conversation_id=conversation_id, root_path=root_path
    )
    if not tree:
        raise ValueError(
            f"No conversation or branches found for conversation_id='{conversation_id}' (harness='{harness}')"
        )
    return json.dumps(tree.model_dump(mode="json", exclude_none=True), indent=2)


@server.tool(
    name="codetalk_diff_branches",
    description="Compare two branches of the same conversation, showing shared steps, divergence point, and distinct steps.",
)
def codetalk_diff_branches(
    conversation_id: str,
    branch_a: str,
    branch_b: str,
    harness: str | None = None,
    root_path: str | None = None,
) -> str:
    """Compare two branches of a conversation."""
    adapter, _ = _find_session(conversation_id, harness, root_path=root_path)
    diff = adapter.diff_branches(
        conversation_id=conversation_id,
        branch_a=branch_a,
        branch_b=branch_b,
        root_path=root_path,
    )
    if not diff:
        raise ValueError(
            f"Could not compute branch diff for branch_a='{branch_a}' and branch_b='{branch_b}' in conversation_id='{conversation_id}'"
        )
    return json.dumps(diff.model_dump(mode="json", exclude_none=True), indent=2)


# ─── Entrypoint ───────────────────────────────────────────────────────────────


@click.command()
@click.option("--transport", default="stdio", help="MCP transport mode (stdio)")
@click.option(
    "--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"
)
def main(transport: str, log_level: str) -> None:
    """CodeTalker MCP server entrypoint."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if transport == "stdio":
        asyncio.run(server.run_stdio_async())
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    main()
