from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

UNSUPPORTED_TOOLS: list[str] = [
    "read_transcript",
    "read_session",
    "get_transcript",
    "get_session",
    "list_transcripts",
    "codetalk_read_transcript",
]

# Full instructions for harnesses with known mid-thread context loss (Freebuff,
# per HARNESS_NOTES): marker-gated so healthy turns do ZERO recovery work.
SERVER_INSTRUCTIONS: str = (
    "Context recovery, marker-gated (v0.3): call codetalk_recover ONLY when a "
    "trigger fires, never on healthy turns. Triggers: (1) this turn contains a "
    "harness marker such as <since_your_last_turn> or <failed_turn> — those "
    "mean prior context was wiped even if the transcript looks continuous; "
    "(2) the user's message has no visible antecedent ('done', 'continue', 'ok', "
    "or a reply to something you cannot see); (3) you find yourself about to "
    "ask the user to re-explain. On a trigger: do NOT reconstruct from files — "
    "call codetalk_recover with the project root, state one line of what you "
    "recovered and from when, then act. Memory verification happens server-side "
    "against an issuance ledger; never append tokens or anchors to your visible "
    "replies, and never invent one. If codetalker fails or returns nothing, say "
    "so and fall back to the project's durable records."
)

# Fallback for harnesses with no known mid-thread loss behavior: short, so the
# mandate is not paid on every turn everywhere.
SERVER_INSTRUCTIONS_FALLBACK: str = (
    "codetalker gives cross-harness transcript access. If a user message has no "
    "visible antecedent ('done', 'continue', 'ok') and context is missing, one "
    "codetalk_recover call with the project root recovers the latest session — "
    "do not ask the user to re-explain. Verification is server-side: never "
    "append returned tokens to visible chat."
)

# Kept for backward compatibility with tests/tools that expect the strict
# per-turn mandate; runtime selection happens in select_instructions below.
SERVER_INSTRUCTIONS_STRICT = SERVER_INSTRUCTIONS


def select_instructions(client_name: str | None) -> str:
    """Tailor server instructions to the connecting client.

    Full marker-gated mandate only for harnesses known to drop in-flight
    context mid-thread; everyone else gets the short fallback.
    """
    if client_name and any(
        frag in client_name.lower() for frag in CLIENT_MARKERS
    ):
        return SERVER_INSTRUCTIONS
    return SERVER_INSTRUCTIONS_FALLBACK


# Substrings of MCP clientInfo.name values that identify context-loss-prone
# harnesses. Freebuff's client identifies as freebuff; extend as evidence
# accumulates (see HARNESS_NOTES for the current catalog).
CLIENT_MARKERS: tuple[str, ...] = ("freebuff",)

TOOL_CATALOG: dict[str, dict[str, str]] = {
    "codetalk_capabilities": {
        "use_when": "First call each agent session; learn harness names, aliases, and ID fields.",
        "do_not_use_when": "Never read MCP JSON schema files from disk instead of this tool.",
    },
    "codetalk_recover": {
        "use_when": (
            "Trigger-gated (v0.3): a turn carrying a wipe marker "
            "(<since_your_last_turn>, <failed_turn>), an antecedent-less user "
            "message ('done', 'continue', 'ok'), or any missing/wiped context. "
            "One call resolves the latest session for a working_directory and "
            "returns its most recent turns plus a fresh continue token."
        ),
        "do_not_use_when": (
            "Healthy turns with intact context — recovery is trigger-gated, so "
            "a normal turn needs NO recovery call. For deep paging of a known "
            "session use codetalk_read / codetalk_filter."
        ),
    },
    "codetalk_recover_token": {
        "use_when": (
            "You hold a codetalker-v3-continue anchor line and must verify your "
            "memory server-side (issuance ledger, transcript fallback) without "
            "loading full turns."
        ),
        "do_not_use_when": (
            "You need the recent turns themselves — codetalk_recover returns "
            "verification and turns together."
        ),
    },
    "codetalk_resolve_session": {
        "use_when": "Agent lost in-harness context and knows the project working_directory.",
        "do_not_use_when": "You already have session_id — use codetalk_read directly.",
    },
    "codetalk_read": {
        "use_when": "Load transcript steps when session_id or working_directory is known.",
        "do_not_use_when": "Searching all sessions for a keyword — use codetalk_search.",
    },
    "codetalk_search": {
        "use_when": "Grep across sessions; find threads by title (match_type=title) or content.",
        "do_not_use_when": "Reading a known session — use codetalk_read. Always pass working_directory or harness when scoped to one project.",
    },
    "codetalk_list": {
        "use_when": "Browse recent session metadata quickly.",
        "do_not_use_when": "Unfiltered list on busy machines — pass working_directory and/or harness.",
    },
    "codetalk_filter": {
        "use_when": "Keyword/type filtering within one session.",
        "do_not_use_when": "Cross-session search — use codetalk_search.",
    },
    "codetalk_info": {
        "use_when": "Metadata only (step counts, paths) without loading bodies.",
        "do_not_use_when": "You need transcript content — use codetalk_read.",
    },
    "codetalk_branches": {
        "use_when": "DAG / fork history when has_dag=true; pass conversation_id.",
        "do_not_use_when": "Linear read of one thread — use codetalk_read.",
    },
    "codetalk_diff_branches": {
        "use_when": "Compare two branch threads in the same conversation.",
        "do_not_use_when": "Single-thread recovery — use codetalk_read.",
    },
}

DECISION_TREE: list[dict[str, str]] = [
    {
        "situation": (
            "ANY turn with missing/wiped context, an antecedent-less user "
            "message ('done', 'continue', 'ok'), or a <since_your_last_turn>/"
            "<failed_turn> marker in the turn"
        ),
        "action": (
            "codetalk_recover(working_directory='<project root>') — ONE call "
            "returns recent turns, memory verification, and a fresh token; "
            "do this BEFORE responding"
        ),
    },
    {
        "situation": "Need deeper history after codetalk_recover",
        "action": "codetalk_read(session_id=..., offset=...) or codetalk_search(search_scope='full')",
    },
    {
        "situation": "Lost context, know project path, need control over the read",
        "action": "codetalk_resolve_session(working_directory=...) → codetalk_read(since_last_user_input=true)",
    },
    {
        "situation": "Know session_id",
        "action": "codetalk_read(session_id=..., harness=... if known)",
    },
    {
        "situation": "Grep all sessions / find by title",
        "action": "codetalk_search(query=..., working_directory=... or harness=..., search_scope='full' for deep history)",
    },
    {
        "situation": "Find OpenCode/Codex thread by title across harnesses",
        "action": "codetalk_search(query='Thread Title') — title matches return match_type='title'; then codetalk_read(session_id=hit.session_id, harness=hit.harness)",
    },
    {
        "situation": "Branch / fork history",
        "action": "codetalk_branches(conversation_id=...) or codetalk_diff_branches(...)",
    },
]


def _resolve_project_root() -> str | None:
    try:
        import codetalker

        pkg_path = Path(codetalker.__file__).resolve().parent
        return str(pkg_path.parent.parent)
    except Exception:
        return None


def _package_version() -> str:
    """Source __version__ is the single source of truth; dist metadata is
    only a fallback. Editable installs don't refresh .dist-info on code
    edits, so metadata can lag the running source (seen live: editable
    dist claimed 0.3.1 while the source was 0.3.2 — the server then lied
    about its own version)."""
    try:
        from codetalker import __version__

        if __version__ and __version__ != "0.0.0":
            return __version__
    except Exception:
        pass
    for dist_name in ("codetalker-mcp", "codetalker"):
        try:
            return version(dist_name)
        except PackageNotFoundError:
            continue
    return "0.0.0"


def build_server_metadata() -> dict[str, Any]:
    pkg_version = _package_version()

    project_root = _resolve_project_root()
    return {
        "version": pkg_version,
        "project_root": project_root,
        "python_entrypoint": sys.executable,
        "stale_path_hint": (
            "If project_root points at .../scratch/codetalker, update MCP config to the "
            "canonical install (e.g. D:/codetalker or your clone path)."
        ),
    }
