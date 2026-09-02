import json
from pathlib import Path

import pytest

from codetalker.adapters.antigravity import AntigravityAdapter
from codetalker.schema import NormalizedSession
from codetalker.search import SearchOptions, search_sessions
from codetalker.server import (
    SessionLookupError,
    codetalk_capabilities,
    codetalk_info,
    codetalk_search,
)
from codetalker.utils.timestamps import compare_timestamps, timestamp_gte
from codetalker.utils.tool_errors import content_indicates_tool_error

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ROLLOUT_FIXTURE = FIXTURES_DIR / "codex_sample_rollout.jsonl"


def test_compare_timestamps_mixed_offset_formats():
    zulu = "2026-09-02T02:38:16+00:00"
    offset = "2026-09-02T03:38:16+01:00"
    assert compare_timestamps(offset, zulu) == 0
    assert timestamp_gte(offset, "2026-09-01") is True
    assert timestamp_gte(offset, "2026-09-03") is False


def test_codetalk_capabilities_v2_fields():
    payload = json.loads(codetalk_capabilities())
    assert "server" in payload
    assert "tool_catalog" in payload
    assert "unsupported_tools" in payload
    assert "read_transcript" in payload["unsupported_tools"]
    assert "decision_tree" in payload["context_recovery"]
    assert payload["search_guidance"]


def test_session_lookup_error_includes_hints():
    with pytest.raises(SessionLookupError) as exc:
        from codetalker.server import _find_session

        _find_session("nonexistent-session-id-xyz", harness="chatgpt", root_path=str(FIXTURES_DIR))
    assert exc.value.hints
    assert any("codetalk_capabilities" in h for h in exc.value.hints)


def test_codetalk_search_working_directory_filter():
    payload = json.loads(
        codetalk_search(
            query="auth",
            harness="chatgpt",
            working_directory=r"C:\Work\project",
            root_path=str(ROLLOUT_FIXTURE),
            limit=10,
        )
    )
    assert payload["match_count"] >= 1
    for hit in payload["results"]:
        assert hit.get("working_directory") == r"C:\Work\project"


def test_codetalk_info_includes_server_metadata():
    payload = json.loads(
        codetalk_info(
            session_id="codex_sample_rollout",
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
        )
    )
    assert payload["server"]["version"]
    assert payload["server"]["project_root"]


def test_content_indicates_tool_error():
    assert content_indicates_tool_error("Session 'abc' not found.")
    assert content_indicates_tool_error('{"isError": true, "error": "boom"}')
    assert not content_indicates_tool_error("All good.")


def test_antigravity_find_transcript_path_from_session_id(tmp_path, monkeypatch):
    adapter = AntigravityAdapter()
    session_id = "test-brain-uuid"
    brain = tmp_path / session_id / ".system_generated" / "logs"
    brain.mkdir(parents=True)
    transcript = brain / "transcript.jsonl"
    transcript.write_text(
        '{"type":"USER_INPUT","content":"<USER_REQUEST>hi</USER_REQUEST>"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "codetalker.adapters.antigravity.os.path.expanduser",
        lambda p: str(tmp_path) if "antigravity/brain" in p.replace("\\", "/") else p,
    )

    session = NormalizedSession(
        session_id=session_id,
        harness="antigravity",
        display_name="Test",
        source_path="/nonexistent/path/transcript.jsonl",
        source_format="jsonl",
    )
    resolved = adapter._find_transcript_path(session)
    assert resolved == str(transcript)


def test_search_title_match_type(monkeypatch):
    from codetalker.schema import NormalizedSession

    session = NormalizedSession(
        session_id="title-session",
        harness="opencode",
        display_name="Codetalker MCP Access",
        source_path=str(FIXTURES_DIR / "chatgpt_dag_export.json"),
        source_format="json",
    )

    class StubAdapter:
        harness_name = "opencode"

        def discover_sessions(self, root_path=None):
            return [session]

        def load_steps(self, session, **kwargs):
            return []

    from codetalker import registry as reg_mod

    monkeypatch.setattr(reg_mod.registry, "get", lambda h: StubAdapter() if h == "opencode" else None)
    monkeypatch.setattr(reg_mod.registry, "list_canonical_harnesses", lambda: ["opencode"])

    hits = search_sessions(
        SearchOptions(query="Codetalker MCP Access", harness="opencode", limit=5)
    )
    assert hits
    assert hits[0]["match_type"] == "title"
