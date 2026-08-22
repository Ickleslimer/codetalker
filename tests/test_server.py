import json
from pathlib import Path

from codetalker.adapters.chatgpt import ChatGPTAdapter
from codetalker.registry import registry
from codetalker.server import (
    codetalk_filter,
    codetalk_info,
    codetalk_list,
    codetalk_read,
    codetalk_search,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FixtureChatGPTAdapter(ChatGPTAdapter):
    """Adapter pointing exclusively to test fixtures for fast unit tests."""

    def discover_sessions(self, root_path: str | None = None):
        return super().discover_sessions(root_path=str(FIXTURES_DIR))


def setup_module():
    # Register fixture-backed adapter for fast unit tests
    test_adapter = FixtureChatGPTAdapter()
    registry.register(test_adapter)


def test_server_list_tools():
    res_str = codetalk_list(harness="chatgpt", limit=10)
    data = json.loads(res_str)
    assert "sessions" in data
    assert "count" in data
    assert data["count"] >= 2  # DAG branches and rollout fixture


def test_server_read_and_filter():
    res_str = codetalk_list(harness="chatgpt", limit=10)
    data = json.loads(res_str)
    assert data["sessions"]

    # Test read trunk
    trunk_sess = next(s for s in data["sessions"] if s.get("branch_root_step_id") is None)
    sess_id = trunk_sess["session_id"]
    harness = trunk_sess["harness"]

    read_str = codetalk_read(session_id=sess_id, harness=harness, since_last_user_input=True)
    read_data = json.loads(read_str)
    assert "session" in read_data
    assert "steps" in read_data
    assert read_data["session"]["session_id"] == sess_id

    # Test filter by actor role
    filter_str = codetalk_filter(session_id=sess_id, harness=harness, actor_roles=["assistant"])
    filter_data = json.loads(filter_str)
    assert "steps" in filter_data
    for s in filter_data["steps"]:
        assert s["actor"]["role"] == "assistant"

    # Test filter by keyword
    kw_str = codetalk_filter(session_id=sess_id, harness=harness, keywords=["auth"])
    kw_data = json.loads(kw_str)
    assert len(kw_data["steps"]) >= 1

    # Test info
    info_str = codetalk_info(session_id=sess_id, harness=harness)
    info_data = json.loads(info_str)
    assert info_data["session_id"] == sess_id


def test_server_search():
    search_str = codetalk_search(query="React", harness="chatgpt", limit=5)
    search_data = json.loads(search_str)
    assert "results" in search_data
    assert len(search_data["results"]) >= 1
    assert search_data["query"] == "React"
