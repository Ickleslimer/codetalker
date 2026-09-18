import json
from pathlib import Path

import pytest

import codetalker.server as server_module
from codetalker.agent_guidance import SERVER_INSTRUCTIONS, TOOL_CATALOG
from codetalker.server import (
    SessionLookupError,
    codetalk_capabilities,
    codetalk_list,
    codetalk_read,
    codetalk_recover,
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
    assert "codetalk_recover" in payload["context_recovery"]["recommended_flow"][0]
    assert "working_directory" in payload["id_guidance"]
    assert "tool_catalog" in payload
    assert "server" in payload


# ─── codetalk_recover: one-call recovery ─────────────────────────────────────


def test_codetalk_recover_returns_session_and_recent_turns():
    payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
        )
    )
    assert payload["recovered"] is True
    assert payload["session"]["session_id"] == "codex_sample_rollout"
    assert payload["session"]["working_directory"] == WORKING_DIR
    assert len(payload["recent_user_turns"]) == 2
    assert payload["recent_user_turns"][-1]["text"] == "Now restart the auth service"
    last = payload["last_assistant_turn"]
    assert last is not None and "Restarting the auth service" in last["text"]
    assert payload["window"]["total_steps_available"] >= len(payload["recent_user_turns"])
    assert "recovered" in payload["next_steps"]


def test_codetalk_recover_respects_user_turn_limit():
    payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            user_turns=1,
        )
    )
    assert len(payload["recent_user_turns"]) == 1
    assert payload["recent_user_turns"][0]["text"] == "Now restart the auth service"


def test_codetalk_recover_trims_long_steps():
    payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            max_step_chars=25,
        )
    )
    assert payload["recent_user_turns"][0]["text"].endswith("…")
    assert len(payload["recent_user_turns"][0]["text"]) <= 26


def test_codetalk_recover_missing_session_raises():
    with pytest.raises(SessionLookupError):
        codetalk_recover(
            working_directory=r"C:\Work\does-not-exist",
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
        )


def test_server_instructions_mandate_per_turn_recovery():
    assert "EVERY turn" in SERVER_INSTRUCTIONS
    assert "codetalk_recover" in SERVER_INSTRUCTIONS
    assert server_module.server.instructions == SERVER_INSTRUCTIONS


def test_tool_catalog_features_codetalk_recover():
    assert "codetalk_recover" in TOOL_CATALOG
    assert "antecedent" in TOOL_CATALOG["codetalk_recover"]["use_when"]
