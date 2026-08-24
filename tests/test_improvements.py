import json
from pathlib import Path
from codetalker.registry import registry
from codetalker.server import codetalk_list, codetalk_read, codetalk_search, codetalk_info, codetalk_branches

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_registry_aliases_and_canonical():
    canonicals = registry.list_canonical_harnesses()
    assert "chatgpt" in canonicals
    assert "antigravity" in canonicals
    assert "cursor" in canonicals
    assert "windsurf" in canonicals
    assert "opencode" in canonicals
    assert "freebuff" in canonicals

    # Verify aliases resolve to same adapter instance
    assert registry.get("codex") is registry.get("chatgpt")
    assert registry.get("devin") is registry.get("windsurf")
    assert registry.get("agy") is registry.get("antigravity")
    assert registry.get("google_antigravity") is registry.get("antigravity")
    assert registry.get("gemini") is registry.get("antigravity")
    assert registry.get("codebuff") is registry.get("freebuff")
    assert registry.get("open_code") is registry.get("opencode")

    # Canonical name resolution
    assert registry.get_canonical_name("codex") == "chatgpt"
    assert registry.get_canonical_name("devin") == "windsurf"
    assert registry.get_canonical_name("chatgpt") == "chatgpt"
    assert registry.get_canonical_name("gemini") == "antigravity"


def test_server_deduplication_and_aliases():
    # Calling list without harness filter should list canonical adapters and not duplicate sessions
    res_str = codetalk_list(limit=20)
    data = json.loads(res_str)

    assert "harnesses" in data
    assert "aliases" in data
    assert len(data["harnesses"]) == len(set(data["harnesses"]))

    # Check for session deduplication
    session_keys = [(s["harness"], s["session_id"]) for s in data["sessions"]]
    assert len(session_keys) == len(set(session_keys))


def test_server_root_path_override():
    # Calling list with explicit root_path parameter
    res_str = codetalk_list(harness="chatgpt", root_path=str(FIXTURES_DIR), limit=10)
    data = json.loads(res_str)
    assert data["count"] >= 2

    # Read with root_path parameter
    sess = data["sessions"][0]
    read_str = codetalk_read(
        session_id=sess["session_id"],
        harness="chatgpt",
        root_path=str(FIXTURES_DIR),
        limit=5,
    )
    read_data = json.loads(read_str)
    assert "session" in read_data
    assert "steps" in read_data


def test_server_fuzzy_and_optional_harness_resolution():
    # Test reading with prefix match and omitted harness
    read_str = codetalk_read(
        session_id="conv-test",
        harness=None,
        root_path=str(FIXTURES_DIR),
        limit=5,
    )
    read_data = json.loads(read_str)
    assert read_data["session"]["session_id"] == "conv-test-uuid-1234"
    assert len(read_data["steps"]) > 0

    # Test reading with URI wrapper
    read_uri_str = codetalk_read(
        session_id="conversation://conv-test-uuid-1234",
        harness="openai",
        root_path=str(FIXTURES_DIR),
    )
    read_uri_data = json.loads(read_uri_str)
    assert read_uri_data["session"]["session_id"] == "conv-test-uuid-1234"

    # Test reading with title match
    read_title_str = codetalk_read(
        session_id="Build a React Auth Hook",
        harness=None,
        root_path=str(FIXTURES_DIR),
    )
    read_title_data = json.loads(read_title_str)
    assert read_title_data["session"]["session_id"] == "conv-test-uuid-1234"

    # Test info and branches with optional harness
    info_str = codetalk_info(session_id="conv-test-uuid-1234", root_path=str(FIXTURES_DIR))
    info_data = json.loads(info_str)
    assert info_data["session_id"] == "conv-test-uuid-1234"

    branches_str = codetalk_branches(conversation_id="conv-test-uuid-1234", root_path=str(FIXTURES_DIR))
    branches_data = json.loads(branches_str)
    assert branches_data["conversation_id"] == "conv-test-uuid-1234"
