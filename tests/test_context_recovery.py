import json
from pathlib import Path

import pytest

import codetalker.server as server_module
from codetalker import continuity as continuity_module
from codetalker.agent_guidance import (
    SERVER_INSTRUCTIONS,
    SERVER_INSTRUCTIONS_FALLBACK,
    TOOL_CATALOG,
    select_instructions,
)
from codetalker.continuity import CONTINUE_TOKEN_PREFIX, parse_continue_token
from codetalker.server import (
    SessionLookupError,
    codetalk_capabilities,
    codetalk_list,
    codetalk_read,
    codetalk_recover,
    codetalk_recover_token,
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


def test_server_instructions_are_trigger_gated():
    # v0.3: the mandate fires on triggers, not on every healthy turn.
    # v0.3.5: verification is server-side — the mandate must NOT ask agents
    # to append the token line to their replies.
    assert "codetalk_recover" in SERVER_INSTRUCTIONS
    assert "issuance ledger" in SERVER_INSTRUCTIONS
    assert "codetalker-v3-continue" not in SERVER_INSTRUCTIONS
    assert server_module.server.instructions == SERVER_INSTRUCTIONS


def test_instructions_tailored_by_client():
    assert select_instructions("freebuff") is SERVER_INSTRUCTIONS
    assert select_instructions("Freebuff Desktop") is SERVER_INSTRUCTIONS
    assert select_instructions("claude-desktop") is SERVER_INSTRUCTIONS_FALLBACK
    assert select_instructions(None) is SERVER_INSTRUCTIONS_FALLBACK
    assert "codetalk_recover" in SERVER_INSTRUCTIONS_FALLBACK
    assert "never" in SERVER_INSTRUCTIONS_FALLBACK


def test_codetalk_recover_token_tool_registered():
    assert "codetalk_recover_token" in TOOL_CATALOG


def test_initialize_tailoring_patch_is_active():
    # v0.3: importing the server patches ServerRunner._handle_initialize so
    # each connection's handshake result is tailored to its clientInfo name.
    from mcp.server.runner import ServerRunner

    assert getattr(ServerRunner._handle_initialize, "_codetalker_tailored", False) is True


def test_tool_catalog_features_codetalk_recover():
    assert "codetalk_recover" in TOOL_CATALOG
    assert "antecedent" in TOOL_CATALOG["codetalk_recover"]["use_when"]


# ─── v0.3.8: continue-token issuance is OPT-IN ─────────────────────────────


@pytest.fixture()
def _isolated_token_ledger(tmp_path, monkeypatch):
    """Keep issuance-ledger writes out of the real ~/.codetalker."""
    monkeypatch.setattr(
        continuity_module, "TOKEN_LEDGER_PATH", str(tmp_path / "tokens.jsonl")
    )


def test_codetalk_recover_omits_token_by_default(_isolated_token_ledger):
    """v0.3.8: the default recover payload ships NO token artifact.

    Rationale: the visible codetalker-v3-continue line repeatedly re-seeded
    echo rituals on clients whose cached schemas still carried the retired
    v0.3 'end your turn with it' mandate — even after the mandate was
    scrubbed from the server (0.3.5) and the tool descriptions (0.3.7).
    """
    payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
        )
    )
    assert payload["recovered"] is True
    assert "continue_token" not in payload


def test_codetalk_recover_issues_token_when_requested(_isolated_token_ledger):
    payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            issue_token=True,
        )
    )
    line = payload.get("continue_token")
    assert isinstance(line, str) and line.startswith(CONTINUE_TOKEN_PREFIX + " ")
    token = parse_continue_token(line)
    assert token is not None
    assert token.session_id == "codex_sample_rollout"
    assert token.working_directory == WORKING_DIR


def test_issued_token_round_trips_through_recover_token(_isolated_token_ledger):
    """An opt-in anchor must still verify via codetalk_recover_token."""
    recover_payload = json.loads(
        codetalk_recover(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            issue_token=True,
        )
    )
    check_payload = json.loads(
        codetalk_recover_token(
            working_directory=WORKING_DIR,
            harness="chatgpt",
            root_path=str(ROLLOUT_FIXTURE),
            claimed_token=recover_payload["continue_token"],
        )
    )
    assert check_payload["memory_check"]["claimed"] is True
    assert check_payload["memory_check"]["matches"] is True
