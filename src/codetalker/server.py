from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import click
from mcp.server.mcpserver import MCPServer

import codetalker.adapters  # noqa: F401
from codetalker.agent_guidance import (
    DECISION_TREE,
    SERVER_INSTRUCTIONS,
    SERVER_INSTRUCTIONS_FALLBACK,
    TOOL_CATALOG,
    UNSUPPORTED_TOOLS,
    build_server_metadata,
    select_instructions,
)
from codetalker.continuity import (
    AnchorCheck,
    ContinueToken,
    detect_session_fresh,
    emit_continue_token,
    parse_continue_token,
    verify_continue_token,
)
from codetalker import continuity as _continuity
from codetalker.registry import registry
from codetalker.schema import ActorRole, BlockType, NormalizedSession, NormalizedStep
from codetalker.search import SearchOptions, normalize_queries, search_sessions
from codetalker.utils.paths import normalize_working_directory, working_directories_match
from codetalker.utils.timestamps import timestamp_gte

logger = logging.getLogger("codetalker.server")

server = MCPServer("codetalker", instructions=SERVER_INSTRUCTIONS)


def _tailor_initialize_result() -> None:
    """Rewrite the handshake result's instructions per connecting client (v0.3).

    `ServerRunner.init_options` is snapshotted per-connection BEFORE any message
    flows, so nothing that mutates server state during the handshake can change
    the result — the only live seam is the result object itself, built by
    `ServerRunner._handle_initialize` from the initialize params (which carry
    `clientInfo`). Patching that static method lets each connection receive
    instructions tailored to its client identity while leaving everything else
    byte-identical.
    """
    from mcp.server.runner import ServerRunner

    if getattr(ServerRunner._handle_initialize, "_codetalker_tailored", False):
        return

    _original = ServerRunner._handle_initialize

    def _tailored(self, params):
        result = _original(self, params)
        client_name = None
        try:
            info = (params or {}).get("clientInfo")
            if isinstance(info, dict):
                client_name = info.get("name")
        except Exception:  # noqa: BLE001 - never break a handshake on probes
            client_name = None
        try:
            result.instructions = select_instructions(client_name)
        except Exception:  # noqa: BLE001
            pass
        return result

    _tailored._codetalker_tailored = True  # type: ignore[attr-defined]
    ServerRunner._handle_initialize = _tailored  # type: ignore[method-assign]


# Apply at import so every connection mode (stdio, SSE, streamable-http)
# inherits the per-client tailoring.
_tailor_initialize_result()

PAYLOAD_WARNING_BYTES = 500_000

HARNESS_NOTES: dict[str, str] = {
    "chatgpt": "Includes OpenAI Codex CLI rollouts and ChatGPT exports; alias: codex.",
    "cursor": (
        "Reads Cursor Composer sessions from state.vscdb. SQLite index is thin — "
        "prefer Codex CLI rollouts for deep cross-session history. Pass working_directory "
        "when list/search returns empty."
    ),
    "antigravity": "Supports subagent branches and DAG fork points.",
    "freebuff": (
        "Full multi-turn logs from Freebuff desktop SQLite. "
        "Freebuff drops in-flight context between turns mid-thread — on restarts "
        "(visible <since_your_last_turn>/<failed_turn> markers) and silently at "
        "message boundaries. Recovery is trigger-gated (v0.3): call "
        "codetalk_recover on a marker or an antecedent-less message only. "
        "MCP requires Freebuff consent sidecar approval after config changes."
    ),
    "opencode": (
        "OpenCode sidecar transcripts live in ~/.local/share/opencode/opencode.db; "
        "desktop drafts.sqlite is prompt-only. Empty sidecar sessions are indexed via "
        "opencode.window.*.dat titles. Use codetalk_search(query='<thread title>') to find sessions."
    ),
    "windsurf": "Devin/Windsurf Cascade protobuf chat state.",
    "claude": "Fixture-tested; requires ~/.claude/projects sessions locally.",
    "aider": "Fixture-tested; reads markdown chat history files.",
    "copilot": "VS Code Copilot Chat session JSONL logs.",
}

CONTEXT_RECOVERY_PLAYBOOK: dict[str, Any] = {
    "problem": (
        "Some harnesses (especially Freebuff) can lose in-flight prompt context while "
        "the full transcript remains on disk."
    ),
    "symptoms": [
        "Assistant says it cannot see session context and asks to continue blindly.",
        "User says 'continue' but the agent has no memory of prior turns.",
        "A turn arrives containing <since_your_last_turn> or <failed_turn> markers.",
    ],
    "recommended_flow": [
        "codetalk_recover(working_directory='<project path>') — ONE call: latest session + its most recent turns",
        "codetalk_resolve_session(working_directory='<project path>', harness='freebuff')",
        "codetalk_read(working_directory='<project path>', harness='freebuff', since_last_user_input=true)",
        "codetalk_search(query=\"can't see the session context\", harness='freebuff') to find affected threads",
    ],
    "notes": [
        "working_directory accepts plain paths or file:// URIs; matching is case-insensitive on Windows.",
        "codetalk_read accepts working_directory instead of session_id for one-shot recovery.",
        "codetalk_recover is the one-call form of resolve + read — prefer it when context is missing mid-thread.",
        "Prefer since_last_user_input=true when the user just said 'continue'.",
        "Recovery is trigger-gated (v0.3): markers or antecedent-less messages trigger; healthy turns do nothing.",
        "Memory is verified server-side against the issuance ledger — no token echo is required (v0.3.5).",
    ],
    "decision_tree": DECISION_TREE,
}

FIXTURE_TESTED_HARNESSES = frozenset({"claude", "aider", "copilot"})


class SessionLookupError(ValueError):
    """Raised when session resolution fails; hints guide agents to the right tool."""

    def __init__(self, message: str, hints: list[str] | None = None):
        hint_text = ""
        if hints:
            hint_text = "\nHints: " + " | ".join(hints)
        super().__init__(message + hint_text)
        self.hints = hints or []


def _session_lookup_hints(
    *,
    session_id: str | None = None,
    working_directory: str | None = None,
    harness: str | None = None,
) -> list[str]:
    hints = [
        "Call codetalk_capabilities first — do not guess tool names like read_transcript.",
    ]
    if session_id and session_id.strip().startswith("conversation://"):
        hints.append("Strip conversation:// prefix or pass the bare UUID as session_id.")
    if working_directory:
        hints.append(
            "Try codetalk_resolve_session(working_directory=..., harness=...) for the latest thread."
        )
    elif session_id:
        hints.append(
            "If session_id came from another harness, pass harness='antigravity'|'chatgpt'|etc."
        )
        hints.append(
            "Or use codetalk_search(query='<title or keyword>', search_scope='full') to re-find the session."
        )
    if not harness:
        hints.append("Pass harness when known to avoid searching the wrong adapter.")
    hints.append("codetalk_read and codetalk_info accept working_directory instead of session_id.")
    return hints


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
    """Safely extract all searchable/filterable text from any ContentBlock."""
    parts: list[str] = []
    t = getattr(b, "text", None)
    if t is not None:
        parts.append(str(t))
    c = getattr(b, "content", None)
    if c is not None:
        parts.append(str(c))
    tn = getattr(b, "tool_name", None)
    if tn is not None:
        parts.append(str(tn))
    ta = getattr(b, "tool_args", None)
    if ta is not None:
        try:
            parts.append(json.dumps(ta))
        except Exception:
            parts.append(str(ta))
    d = getattr(b, "diff", None)
    if d is not None:
        parts.append(str(d))
    uri = getattr(b, "file_uri", None)
    if uri is not None:
        parts.append(str(uri))
    nm = getattr(b, "name", None)
    if nm is not None:
        parts.append(str(nm))
    p = getattr(b, "path", None)
    if p is not None:
        parts.append(str(p))
    ev = getattr(b, "event_name", None)
    if ev is not None:
        parts.append(str(ev))
    dt = getattr(b, "detail", None)
    if dt is not None:
        parts.append(str(dt))
    return " ".join(parts)


def _resolve_exclude_roles(
    conversation_only: bool,
    exclude_actor_roles: list[str] | None,
) -> list[ActorRole] | None:
    excluded: list[ActorRole] = []
    if conversation_only:
        excluded.append(ActorRole.SYSTEM)
    if exclude_actor_roles:
        for role in exclude_actor_roles:
            key = role.lower()
            if key in ActorRole.__members__.values():
                excluded.append(ActorRole(key))
    return excluded or None


def _payload_meta(payload: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(payload)
    approx_bytes = len(serialized.encode("utf-8"))
    meta: dict[str, Any] = {"approx_response_bytes": approx_bytes}
    if approx_bytes > PAYLOAD_WARNING_BYTES:
        meta["payload_warning"] = (
            f"Response is ~{approx_bytes // 1024}KB. "
            "Re-fetch with tighter limit, conversation_only=true, include_raw_data=false, "
            "or max_step_chars."
        )
    return meta


def _session_summary(s: NormalizedSession) -> dict[str, Any]:
    return {
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
        "is_empty": s.is_empty,
        "coverage_warning": s.coverage_warning,
        "display_name_truncated": s.display_name_truncated,
    }


def _search_candidate_sessions(
    clean_id_lower: str, candidate_adapters: list[Any], root_path: str | None = None
) -> tuple[Any, NormalizedSession] | None:
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

    for ad in candidate_adapters:
        try:
            sessions = ad.discover_sessions(root_path=root_path)
            for s in sessions[:20]:
                try:
                    steps = ad.load_steps(
                        session=s,
                        limit=10,
                        from_end=True,
                        include_raw_data=False,
                    )
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

    res = _search_candidate_sessions(clean_id_lower, candidate_adapters, root_path=root_path)
    if res:
        return res

    if root_path is not None:
        res = _search_candidate_sessions(clean_id_lower, candidate_adapters, root_path=None)
        if res:
            return res

    h_msg = f" for harness '{harness}'" if harness else " across all harnesses"
    raise SessionLookupError(
        f"Session '{session_id}' not found{h_msg}.",
        hints=_session_lookup_hints(session_id=session_id, harness=harness),
    )


def _sessions_for_working_directory(
    working_directory: str,
    harness: str | None = None,
    root_path: str | None = None,
) -> list[NormalizedSession]:
    all_sessions, _ = _discover_all_sessions(harness, root_path)
    matches = [
        s
        for s in all_sessions
        if working_directories_match(s.working_directory, working_directory)
    ]
    matches.sort(
        key=lambda s: (
            s.last_activity or s.started_at or "",
            1 if s.branch_root_step_id else 0,
        ),
        reverse=True,
    )
    return matches


def _adapter_for_session(session: NormalizedSession) -> Any:
    adapter = registry.get(session.harness)
    if not adapter:
        raise ValueError(f"No adapter registered for harness '{session.harness}'.")
    return adapter


def _resolve_session_by_working_directory(
    working_directory: str,
    harness: str | None = None,
    root_path: str | None = None,
    display_name: str | None = None,
) -> tuple[Any, NormalizedSession]:
    matches = _sessions_for_working_directory(working_directory, harness, root_path)
    if display_name and display_name.strip():
        name_lower = display_name.strip().lower()
        titled = [s for s in matches if s.display_name and name_lower in s.display_name.lower()]
        if titled:
            matches = titled
    if not matches:
        h_msg = f" for harness '{harness}'" if harness else ""
        normalized = normalize_working_directory(working_directory)
        extra_hints: list[str] = []
        if display_name:
            extra_hints.append(f"No session title matched display_name='{display_name}'.")
        raise SessionLookupError(
            f"No session found for working_directory='{working_directory}'"
            f"{h_msg}. Normalized query: '{normalized}'.",
            hints=_session_lookup_hints(
                working_directory=working_directory,
                harness=harness,
            )
            + extra_hints,
        )
    session = matches[0]
    return _adapter_for_session(session), session


def _resolve_session_ref(
    session_id: str | None,
    harness: str | None = None,
    root_path: str | None = None,
    working_directory: str | None = None,
    display_name: str | None = None,
) -> tuple[Any, NormalizedSession]:
    if session_id and session_id.strip():
        return _find_session(session_id, harness, root_path=root_path)
    if working_directory and working_directory.strip():
        return _resolve_session_by_working_directory(
            working_directory,
            harness,
            root_path=root_path,
            display_name=display_name,
        )
    raise SessionLookupError(
        "Provide session_id or working_directory.",
        hints=_session_lookup_hints(harness=harness),
    )


def _discover_all_sessions(
    harness: str | None,
    root_path: str | None,
) -> tuple[list[NormalizedSession], dict[str, int]]:
    if harness:
        harnesses = [harness]
    else:
        harnesses = registry.list_canonical_harnesses()

    all_sessions: list[NormalizedSession] = []
    seen_keys: set[tuple[str, str]] = set()
    per_harness_counts: dict[str, int] = {h: 0 for h in registry.list_canonical_harnesses()}

    for h in harnesses:
        adapter = registry.get(h)
        if adapter:
            try:
                discovered = adapter.discover_sessions(root_path=root_path)
                canonical = registry.get_canonical_name(h)
                per_harness_counts[canonical] = per_harness_counts.get(canonical, 0) + len(
                    discovered
                )
                for s in discovered:
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

    return all_sessions, per_harness_counts


def _build_harness_status(per_harness_counts: dict[str, int]) -> dict[str, dict[str, Any]]:
    status: dict[str, dict[str, Any]] = {}
    for h in registry.list_canonical_harnesses():
        count = per_harness_counts.get(h, 0)
        if count > 0:
            st = "ok"
        elif h in FIXTURE_TESTED_HARNESSES:
            st = "no_local_data_fixture_tested"
        else:
            st = "no_local_data"
        status[h] = {
            "registered": True,
            "session_count": count,
            "status": st,
            "note": HARNESS_NOTES.get(h),
        }
    return status


# ─── Tools ────────────────────────────────────────────────────────────────────


@server.tool(
    name="codetalk_capabilities",
    description=(
        "List supported harnesses, aliases, and agent-oriented ID guidance. "
        "Call once per session before other codetalk_* tools."
    ),
)
def codetalk_capabilities() -> str:
    """Return harness capabilities and ID usage guidance."""
    payload = {
        "server": build_server_metadata(),
        "harnesses": registry.list_canonical_harnesses(),
        "aliases": registry.list_aliases(),
        "harness_notes": HARNESS_NOTES,
        "tool_catalog": TOOL_CATALOG,
        "unsupported_tools": UNSUPPORTED_TOOLS,
        "id_guidance": {
            "session_id": "Use for codetalk_read, codetalk_filter, codetalk_info.",
            "working_directory": (
                "Use with codetalk_resolve_session or pass to codetalk_read instead of "
                "session_id when the agent lost in-harness context."
            ),
            "conversation_id": (
                "Use for codetalk_branches and codetalk_diff_branches. "
                "Codex rollouts may use a prefixed conversation_id distinct from session_id."
            ),
            "branch_id": "Usually equals session_id for branch threads.",
        },
        "context_recovery": CONTEXT_RECOVERY_PLAYBOOK,
        "continuity_v3": (
            "Recovery is trigger-gated: call codetalk_recover only on a wipe "
            "marker (<since_your_last_turn>, <failed_turn>) or an antecedent-less "
            "user message — never on healthy turns. If codetalk_recover hands "
            "you a token line, hold it privately and pass it back as "
            "claimed_token if verification is ever needed; the server verifies "
            "it against its issuance ledger (codetalk_recover_token = "
            "verification only). Nothing ever goes in visible chat."
        ),
        "one_call_recovery": (
            "codetalk_recover(working_directory=...) = resolve + read recent turns "
            "in ONE call. Use it on ANY turn with missing/wiped context or an "
            "antecedent-less user message ('done', 'continue', 'ok') BEFORE responding."
        ),
        "recommended_read_defaults": {
            "from_end": True,
            "conversation_only": True,
            "include_raw_data": False,
            "limit": 30,
        },
        "search_guidance": (
            "Always pass working_directory or harness when searching a known project. "
            "Use search_scope='full' for deep history. Title matches return match_type='title'."
        ),
    }
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_list",
    description="List conversation sessions and branch threads across one or all coding harnesses.",
)
def codetalk_list(
    harness: str | None = None,
    conversation_id: str | None = None,
    working_directory: str | None = None,
    since: str | None = None,
    limit: int = 50,
    root_path: str | None = None,
    include_capabilities: bool = False,
    include_harness_status: bool = True,
) -> str:
    """List session shells (metadata only, fast)."""
    all_sessions, per_harness_counts = _discover_all_sessions(harness, root_path)

    if conversation_id:
        all_sessions = [
            s
            for s in all_sessions
            if s.conversation_id == conversation_id or s.session_id == conversation_id
        ]

    if working_directory:
        all_sessions = [
            s
            for s in all_sessions
            if working_directories_match(s.working_directory, working_directory)
        ]

    if since:
        all_sessions = [
            s
            for s in all_sessions
            if timestamp_gte(s.last_activity or s.started_at or "", since)
        ]

    all_sessions.sort(key=lambda s: s.last_activity or s.started_at or "", reverse=True)

    if limit > 0:
        all_sessions = all_sessions[:limit]

    summaries = [_session_summary(s) for s in all_sessions]

    payload: dict[str, Any] = {
        "count": len(summaries),
        "sessions": summaries,
    }
    if include_capabilities:
        payload["harnesses"] = registry.list_canonical_harnesses()
        payload["aliases"] = registry.list_aliases()
    if include_harness_status and harness is None:
        payload["harness_status"] = _build_harness_status(per_harness_counts)

    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_resolve_session",
    description=(
        "Resolve the most recent session for a project working_directory. "
        "Use when an in-harness agent lost context and does not know session_id "
        "(common with Freebuff)."
    ),
)
def codetalk_resolve_session(
    working_directory: str,
    harness: str | None = None,
    display_name: str | None = None,
    root_path: str | None = None,
    limit: int = 5,
) -> str:
    """Resolve the latest session for a workspace path."""
    matches = _sessions_for_working_directory(working_directory, harness, root_path)
    if display_name and display_name.strip():
        name_lower = display_name.strip().lower()
        titled = [s for s in matches if s.display_name and name_lower in s.display_name.lower()]
        if titled:
            matches = titled
    if not matches:
        h_msg = f" for harness '{harness}'" if harness else ""
        normalized = normalize_working_directory(working_directory)
        extra_hints: list[str] = []
        if display_name:
            extra_hints.append(
                f"Try codetalk_search(query='{display_name}', search_scope='full') across harnesses."
            )
        raise SessionLookupError(
            f"No session found for working_directory='{working_directory}'"
            f"{h_msg}. Normalized query: '{normalized}'.",
            hints=_session_lookup_hints(
                working_directory=working_directory,
                harness=harness,
            )
            + extra_hints,
        )

    primary = matches[0]
    alternates = matches[1 : max(limit, 1)] if limit > 0 else []

    payload: dict[str, Any] = {
        "resolved": True,
        "working_directory_query": working_directory,
        "normalized_working_directory": normalize_working_directory(working_directory),
        "session": _session_summary(primary),
        "match_count": len(matches),
        "alternate_sessions": [_session_summary(s) for s in alternates],
        "recovery_hint": (
            "Call codetalk_read with session_id from session, or pass the same "
            "working_directory to codetalk_read (optionally since_last_user_input=true)."
        ),
    }
    return json.dumps(payload, indent=2)


def _iso_timestamp(ts: Any) -> Any:
    """JSON-safe timestamp: datetime -> isoformat, anything else passes through."""
    return ts.isoformat() if hasattr(ts, "isoformat") else ts


@server.tool(
    name="codetalk_recover",
    description=(
        "ONE-CALL context recovery for any turn where context is missing, wiped, "
        "or a user message has no visible antecedent ('done', 'continue', 'ok', "
        "or a reply to something you cannot see). Resolves the latest session for "
        "a working_directory and returns its most recent user turns plus the last "
        "assistant turn in a single step. Trigger-gated: a wipe marker "
        "(<since_your_last_turn>, <failed_turn>) or an antecedent-less message "
        "is the trigger — healthy turns need no call. Optionally pass claimed_token "
        "(a codetalker-v3-continue line you were given earlier) to have your memory "
        "verified against the server's issuance ledger. Nothing needs to be "
        "appended to your replies."
    ),
)
def codetalk_recover(
    working_directory: str,
    harness: str | None = None,
    root_path: str | None = None,
    display_name: str | None = None,
    user_turns: int = 6,
    max_step_chars: int = 1200,
    claimed_token: str | None = None,
) -> str:
    """Resolve the latest session for a workspace and return its recent turns."""
    adapter, session = _resolve_session_by_working_directory(
        working_directory,
        harness,
        root_path=root_path,
        display_name=display_name,
    )

    anchor_check: AnchorCheck | None = None
    token: ContinueToken | None = None
    if claimed_token:
        token = parse_continue_token(claimed_token)
        if token is None:
            anchor_check = AnchorCheck(
                matches=False,
                reason=(
                    "claimed_token is absent, malformed, or signature-invalid — "
                    "treat your memory as unverified"
                ),
            )
    # Window sized to comfortably contain the requested user turns; steps come
    # back oldest-first within the window, so user turns are trimmed from the
    # front and the assistant turn overwrites forward to the most recent.
    steps, pagination = adapter.load_steps_paginated(
        session=session,
        since=None,
        until=None,
        since_last_user_input=False,
        exclude_actor_roles=[ActorRole.SYSTEM],
        include_thinking=False,
        include_raw_data=False,
        max_step_chars=max_step_chars,
        offset=0,
        from_end=True,
        limit=max(60, int(user_turns) * 8),
    )

    recent_user: list[dict[str, Any]] = []
    last_assistant: dict[str, Any] | None = None
    for step in steps:
        role = step.actor.role
        for block in step.blocks:
            if getattr(block, "type", None) is not BlockType.TEXT:
                continue
            text = getattr(block, "text", None)
            if not text:
                continue
            if role == ActorRole.USER:
                recent_user.append(
                    {
                        "step_index": step.step_index,
                        "timestamp": _iso_timestamp(step.timestamp),
                        "text": text,
                    }
                )
            elif role == ActorRole.ASSISTANT:
                last_assistant = {
                    "step_index": step.step_index,
                    "timestamp": _iso_timestamp(step.timestamp),
                    "text": text,
                }
    recent_user = recent_user[-max(int(user_turns), 1) :]

    # Mechanical freshness classification over the scanned window — turns the
    # harness restarted or failed mid-response carry visible markers.
    freshness = detect_session_fresh(steps)

    if anchor_check is None and token is not None:
        anchor_check = verify_continue_token(
            token,
            steps,
            session_id=session.session_id,
            total_steps=pagination.total_steps_available,
        )

    last_user_text = recent_user[-1]["text"] if recent_user else ""
    continue_token_line = emit_continue_token(
        session_id=session.session_id,
        working_directory=working_directory,
        last_user_step_index=recent_user[-1]["step_index"] if recent_user else None,
        total_steps=pagination.total_steps_available,
        last_user_text=last_user_text,
    )

    payload: dict[str, Any] = {
        "recovered": True,
        "working_directory_query": working_directory,
        "session": _session_summary(session),
        "recent_user_turns": recent_user,
        "last_assistant_turn": last_assistant,
        "window": {
            "returned_step_count": len(steps),
            "total_steps_available": pagination.total_steps_available,
            "start_step_index": pagination.start_step_index,
            "end_step_index": pagination.end_step_index,
        },
        "freshness": freshness.to_payload(),
        "memory_check": (
            {
                "claimed": True,
                "matches": anchor_check.matches,
                "reason": anchor_check.reason,
            }
            if anchor_check
            else {"claimed": False}
        ),
        "continue_token": continue_token_line,
        "next_steps": (
            "State one line of what you recovered and from when, then act. For "
            "deeper history: codetalk_read(session_id=<session.session_id>, "
            "offset=...) or codetalk_search(search_scope='full'). If these turns "
            "do not explain the user's message, page further back BEFORE acting. "
            "If your memory of these turns matches what you recovered, act on it; "
            "no token needs to be echoed anywhere."
        ),
    }
    payload.update(_payload_meta(payload))
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_recover_token",
    description=(
        "Verify a codetalker-v3-continue anchor against the server's issuance "
        "ledger (with a transcript fallback for pre-0.3.5 tokens) without "
        "loading full turns. Pass the token line you were given plus the project "
        "working_directory. Use when you need to confirm your memory is real "
        "before acting on it; codetalk_recover is the fuller form (recent turns "
        "plus a fresh token)."
    ),
)
def codetalk_recover_token(
    working_directory: str,
    claimed_token: str,
    harness: str | None = None,
    root_path: str | None = None,
    display_name: str | None = None,
) -> str:
    """Verify the agent's claimed continue anchor against stored history."""
    adapter, session = _resolve_session_by_working_directory(
        working_directory,
        harness,
        root_path=root_path,
        display_name=display_name,
    )
    token = parse_continue_token(claimed_token or "")
    if token is None:
        check = AnchorCheck(
            matches=False,
            reason="token absent, malformed, or signature-invalid",
        )
        issued_here = False
    else:
        issued_here = _continuity.record_issued_anchor(claimed_token)
        if not issued_here:
            logger.warning(
                "token issuance ledger write failed at %s; falling back to "
                "transcript-only verification",
                _continuity.TOKEN_LEDGER_PATH,
            )
        steps, pagination = adapter.load_steps_paginated(
            session=session,
            since=None,
            until=None,
            since_last_user_input=False,
            exclude_actor_roles=[ActorRole.SYSTEM],
            include_thinking=False,
            include_raw_data=False,
            max_step_chars=400,
            offset=0,
            from_end=True,
            limit=200,
        )
        check = verify_continue_token(
            token,
            steps,
            session_id=session.session_id,
            total_steps=pagination.total_steps_available,
            ledger_path=None if issued_here else os.devnull,
        )
    return json.dumps(
        {
            "session": _session_summary(session),
            "memory_check": {
                "claimed": True,
                "matches": check.matches,
                "reason": check.reason,
            },
        },
        indent=2,
    )


@server.tool(
    name="codetalk_read",
    description=(
        "Read normalized transcript steps for a session thread. "
        "Defaults return the most recent conversation turns (from_end=true). "
        "Provide session_id, or working_directory when session_id is unknown."
    ),
)
def codetalk_read(
    session_id: str = "",
    harness: str | None = None,
    working_directory: str | None = None,
    since: str | None = None,
    until: str | None = None,
    since_last_user_input: bool = False,
    conversation_only: bool = True,
    exclude_actor_roles: list[str] | None = None,
    include_thinking: bool = True,
    include_raw_data: bool = False,
    max_step_chars: int | None = None,
    offset: int = 0,
    from_end: bool = True,
    limit: int = 30,
    root_path: str | None = None,
) -> str:
    """Read normalized steps for a given session."""
    adapter, session = _resolve_session_ref(
        session_id=session_id,
        harness=harness,
        root_path=root_path,
        working_directory=working_directory,
    )
    excluded = _resolve_exclude_roles(conversation_only, exclude_actor_roles)

    steps, pagination = adapter.load_steps_paginated(
        session=session,
        since=since,
        until=until,
        since_last_user_input=since_last_user_input,
        exclude_actor_roles=excluded,
        include_thinking=include_thinking,
        include_raw_data=include_raw_data,
        max_step_chars=max_step_chars,
        offset=offset,
        from_end=from_end,
        limit=limit if limit and limit > 0 else 30,
    )

    total_steps = pagination.total_steps_available
    if total_steps == 0:
        try:
            total_steps = adapter.count_steps(session)
        except Exception:
            total_steps = session.step_count

    payload: dict[str, Any] = {
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
            "total_steps_in_thread": total_steps,
            "user_turn_count": session.user_turn_count,
            "assistant_turn_count": session.assistant_turn_count,
            "returned_step_count": len(steps),
            "returned_step_range": {
                "start_index": pagination.start_step_index,
                "end_index": pagination.end_step_index,
            },
            "coverage_warning": session.coverage_warning,
        },
        "pagination": pagination.model_dump(mode="json"),
        "steps": [s.model_dump(mode="json", exclude_none=True) for s in steps],
    }
    if not steps and (total_steps or 0) > 0:
        payload["read_warning"] = (
            f"Session has {total_steps} steps but none matched filters. "
            "Try widening limit, setting conversation_only=false, or check source_path."
        )
    elif not steps and not (total_steps or session.step_count):
        payload["read_warning"] = (
            "No steps loaded — transcript file may be missing or unreadable. "
            "Call codetalk_info and verify harness/source_path; try codetalk_search to re-find."
        )
    payload.update(_payload_meta(payload))
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_filter",
    description="Filter steps in a session by keywords, step types, or actor roles.",
)
def codetalk_filter(
    session_id: str = "",
    harness: str | None = None,
    working_directory: str | None = None,
    keywords: list[str] | None = None,
    step_types: list[str] | None = None,
    actor_roles: list[str] | None = None,
    conversation_only: bool = False,
    exclude_actor_roles: list[str] | None = None,
    since_last_user_input: bool = False,
    include_thinking: bool = True,
    include_raw_data: bool = False,
    max_step_chars: int | None = None,
    offset: int = 0,
    from_end: bool = True,
    limit: int | None = None,
    root_path: str | None = None,
) -> str:
    """Filter steps in a session."""
    adapter, session = _resolve_session_ref(
        session_id=session_id,
        harness=harness,
        root_path=root_path,
        working_directory=working_directory,
    )

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

    excluded = _resolve_exclude_roles(conversation_only, exclude_actor_roles)

    steps, pagination = adapter.load_steps_paginated(
        session=session,
        since_last_user_input=since_last_user_input,
        include_step_types=parsed_step_types,
        include_actor_roles=parsed_actor_roles,
        exclude_actor_roles=excluded,
        include_thinking=include_thinking,
        include_raw_data=include_raw_data,
        max_step_chars=max_step_chars,
        offset=offset,
        from_end=from_end,
        limit=limit,
    )

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
        pagination = pagination.model_copy(
            update={"returned_step_count": len(steps)}
        )

    payload: dict[str, Any] = {
        "session_id": session.session_id,
        "harness": session.harness,
        "returned_step_count": len(steps),
        "pagination": pagination.model_dump(mode="json"),
        "steps": [s.model_dump(mode="json", exclude_none=True) for s in steps],
    }
    payload.update(_payload_meta(payload))
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_search",
    description=(
        "Search across all sessions and transcripts for a query string. "
        "Use search_scope='full' for entire threads; defaults scan recent tail only."
    ),
)
def codetalk_search(
    query: str,
    harness: str | None = None,
    working_directory: str | None = None,
    since: str | None = None,
    limit: int = 20,
    max_sessions_to_search: int = 30,
    max_hits_per_session: int = 1,
    search_scope: str = "tail",
    queries: list[str] | None = None,
    match_tool_names: bool = True,
    include_context_steps: int = 0,
    root_path: str | None = None,
) -> str:
    """Search for query in session transcripts efficiently."""
    options = SearchOptions(
        query=query,
        queries=queries,
        harness=harness,
        working_directory=working_directory,
        since=since,
        limit=limit,
        max_sessions_to_search=max_sessions_to_search,
        max_hits_per_session=max_hits_per_session,
        search_scope=search_scope,
        match_tool_names=match_tool_names,
        include_context_steps=include_context_steps,
        root_path=root_path,
    )
    matches = search_sessions(options)
    payload: dict[str, Any] = {
        "query": query,
        "queries": normalize_queries(query, queries),
        "search_scope": search_scope,
        "match_count": len(matches),
        "results": matches,
    }
    if (
        not working_directory
        and not harness
        and limit > 0
        and len(matches) >= limit
    ):
        payload["warning"] = (
            "Result cap reached without working_directory or harness filter. "
            "Re-run with working_directory='<project path>' for project-scoped search."
        )
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_info",
    description="Get metadata for a specific session without loading step bodies.",
)
def codetalk_info(
    session_id: str = "",
    harness: str | None = None,
    working_directory: str | None = None,
    root_path: str | None = None,
) -> str:
    """Get metadata for a session."""
    adapter, session = _resolve_session_ref(
        session_id=session_id,
        harness=harness,
        root_path=root_path,
        working_directory=working_directory,
    )
    try:
        session.step_count = adapter.count_steps(session)
    except Exception:
        pass
    payload = session.model_dump(mode="json", exclude={"steps"})
    payload["server"] = build_server_metadata()
    return json.dumps(payload, indent=2)


@server.tool(
    name="codetalk_branches",
    description=(
        "Get the DAG branch tree, fork points, and subagent hierarchy for a conversation. "
        "Use conversation_id; branch_id usually equals session_id."
    ),
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
    description=(
        "Compare two branches of the same conversation. "
        "Defaults to summary_only (counts/metadata only). "
        "branch_id usually equals session_id."
    ),
)
def codetalk_diff_branches(
    conversation_id: str,
    branch_a: str,
    branch_b: str,
    harness: str | None = None,
    summary_only: bool = True,
    include_raw_data: bool = False,
    limit_per_branch: int = 20,
    from_end: bool = True,
    root_path: str | None = None,
) -> str:
    """Compare two branches of a conversation."""
    adapter, _ = _find_session(conversation_id, harness, root_path=root_path)
    diff = adapter.diff_branches(
        conversation_id=conversation_id,
        branch_a=branch_a,
        branch_b=branch_b,
        root_path=root_path,
        summary_only=summary_only,
        include_raw_data=include_raw_data,
        limit_per_branch=limit_per_branch,
        from_end=from_end,
    )
    if not diff:
        raise ValueError(
            f"Could not compute branch diff for branch_a='{branch_a}' and branch_b='{branch_b}' "
            f"in conversation_id='{conversation_id}'"
        )
    payload = diff.model_dump(mode="json", exclude_none=True)
    payload.update(_payload_meta(payload))
    return json.dumps(payload, indent=2)


@click.group(invoke_without_command=True)
@click.option("--transport", default="stdio", help="MCP transport mode (stdio)")
@click.option(
    "--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)"
)
@click.pass_context
def main(ctx: click.Context, transport: str, log_level: str) -> None:
    """CodeTalker MCP server and audit utilities."""
    if ctx.invoked_subcommand is None:
        _run_server(transport, log_level)


def _run_server(transport: str, log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if transport == "stdio":
        asyncio.run(server.run_stdio_async())
    else:
        raise ValueError(f"Unsupported transport: {transport}")


@main.command("audit")
@click.option("--since", default="2026-08-22", show_default=True)
@click.option(
    "--search-scope",
    type=click.Choice(["tail", "full"], case_sensitive=False),
    default="full",
    show_default=True,
)
@click.option(
    "--queries",
    default="codetalker,codetalk_,code talker,mcp__codetalker,user-codetalker",
    show_default=True,
)
@click.option("--harness", default=None)
@click.option("--root-path", default=None)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=".audit/codetalker-usage-hits.jsonl",
    show_default=True,
)
@click.option("--include-context-steps", default=2, show_default=True)
def audit_subcommand(
    since: str,
    search_scope: str,
    queries: str,
    harness: str | None,
    root_path: str | None,
    output: Path,
    include_context_steps: int,
) -> None:
    """Batch-search local transcripts for CodeTalker mentions."""
    from codetalker.audit import run_audit

    summary = run_audit(
        since=since,
        search_scope=search_scope,
        queries=queries,
        harness=harness,
        root_path=root_path,
        output=output,
        include_context_steps=include_context_steps,
    )
    click.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
