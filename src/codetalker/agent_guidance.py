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

TOOL_CATALOG: dict[str, dict[str, str]] = {
    "codetalk_capabilities": {
        "use_when": "First call each agent session; learn harness names, aliases, and ID fields.",
        "do_not_use_when": "Never read MCP JSON schema files from disk instead of this tool.",
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
        "situation": "Lost context, know project path",
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


def build_server_metadata() -> dict[str, Any]:
    pkg_version = "0.0.0"
    try:
        pkg_version = version("codetalker")
    except PackageNotFoundError:
        pass

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
