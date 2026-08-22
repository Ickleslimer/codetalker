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


def _get_adapter(harness: str):
    adapter = registry.get(harness)
    if not adapter:
        raise ValueError(
            f"Unknown harness '{harness}'. Registered harnesses: {registry.list_harnesses()}"
        )
    return adapter


def _find_session(
    session_id: str, harness: str, root_path: str | None = None
) -> tuple[Any, NormalizedSession]:
    adapter = _get_adapter(harness)
    sessions = adapter.discover_sessions(root_path=root_path)
    for s in sessions:
        if s.session_id == session_id:
            return adapter, s
    # Also check if session_id matches conversation_id
    for s in sessions:
        if s.conversation_id == session_id and s.branch_root_step_id is None:
            return adapter, s
    raise ValueError(
        f"Session '{session_id}' not found for harness '{harness}' (checked {len(sessions)} discovered sessions)"
    )


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
    # If harness is specified, query only that harness (resolved to canonical)
    # If not specified, query each canonical adapter ONCE to avoid duplicate listings across aliases
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
    description="Read and normalize transcript steps for a specific session thread.",
)
def codetalk_read(
    session_id: str,
    harness: str,
    since: str | None = None,
    until: str | None = None,
    since_last_user_input: bool = False,
    include_thinking: bool = True,
    limit: int | None = None,
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
        limit=limit,
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
    harness: str,
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
                if hasattr(b, "text"):
                    step_text += " " + getattr(b, "text", "")
                if hasattr(b, "content"):
                    step_text += " " + getattr(b, "content", "")
                if hasattr(b, "tool_name"):
                    step_text += " " + getattr(b, "tool_name", "")
                if hasattr(b, "tool_args"):
                    step_text += " " + json.dumps(getattr(b, "tool_args", {}))

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
                            text_val = ""
                            if hasattr(b, "text"):
                                text_val = getattr(b, "text", "")
                            elif hasattr(b, "content"):
                                text_val = getattr(b, "content", "")
                            elif hasattr(b, "tool_name"):
                                text_val = (
                                    getattr(b, "tool_name", "")
                                    + " "
                                    + json.dumps(getattr(b, "tool_args", {}))
                                )

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
                                        "step_index": s.step_index,
                                        "actor_role": s.actor.role.value,
                                        "block_type": (
                                            b.type.value
                                            if hasattr(b.type, "value")
                                            else str(b.type)
                                        ),
                                        "preview": f"...{preview}...",
                                        "timestamp": s.timestamp,
                                    }
                                )
                                if len(matches) >= limit:
                                    break
                        if len(matches) >= limit:
                            break
                except Exception as e:
                    logger.debug(
                        f"Error loading steps for search in session '{sess.session_id}': {e}"
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
    session_id: str, harness: str, root_path: str | None = None
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
    harness: str,
    root_path: str | None = None,
) -> str:
    """Get the full DAG branch hierarchy for a conversation."""
    adapter = _get_adapter(harness)
    tree = adapter.get_branch_tree(conversation_id=conversation_id, root_path=root_path)
    if not tree:
        raise ValueError(
            f"No conversation or branches found for conversation_id='{conversation_id}' in harness='{harness}'"
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
    harness: str,
    root_path: str | None = None,
) -> str:
    """Compare two branches of a conversation."""
    adapter = _get_adapter(harness)
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
@click.option("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
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
