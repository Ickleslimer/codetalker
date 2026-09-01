import json
from pathlib import Path

from codetalker.server import (
    codetalk_capabilities,
    codetalk_list,
    codetalk_read,
    codetalk_resolve_session,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ROLLOUT_FIXTURE = FIXTURES_DIR / "codex_sample_rollout.jsonl"
WORKING_DIR = r"C:\Work\project"


def test_codetalk_resolve_session_by_working_directory():
    payload = json.loads(
        codetalk_resolve_session(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
        )
    )
    assert payload["resolved"] is True
    assert payload["session"]["working_directory"] == WORKING_DIR
    assert payload["session"]["session_id"] == "codex_sample_rollout"
    assert payload["match_count"] >= 1


def test_codetalk_read_by_working_directory():
    payload = json.loads(
        codetalk_read(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            limit=5,
            since_last_user_input=True,
        )
    )
    assert payload["session"]["session_id"] == "codex_sample_rollout"
    assert payload["session"]["working_directory"] == WORKING_DIR
    assert len(payload["steps"]) > 0


def test_codetalk_list_filters_by_working_directory():
    payload = json.loads(
        codetalk_list(
            harness="chatgpt",
            working_directory=WORKING_DIR,
            root_path=str(ROLLOUT_FIXTURE),
            limit=10,
        )
    )
    assert payload["count"] == 1
    assert payload["sessions"][0]["working_directory"] == WORKING_DIR


def test_codetalk_capabilities_includes_context_recovery():
    payload = json.loads(codetalk_capabilities())
    assert "context_recovery" in payload
    assert "freebuff" in payload["context_recovery"]["recommended_flow"][0]
    assert "working_directory" in payload["id_guidance"]
