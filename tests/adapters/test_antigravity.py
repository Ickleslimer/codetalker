import os
from pathlib import Path
from codetalker.adapters.antigravity import AntigravityAdapter, _clean_user_prompt
from codetalker.schema import ActorRole, TextBlock, ToolCallBlock, ThinkingBlock


def test_clean_user_prompt():
    raw = "<USER_REQUEST>\nFix the button layout\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime=123\n</ADDITIONAL_METADATA>"
    clean = _clean_user_prompt(raw)
    assert clean == "Fix the button layout"


def test_antigravity_adapter_discovery_and_read():
    adapter = AntigravityAdapter()
    sessions = adapter.discover_sessions()

    # Should discover active brain sessions on machine
    assert len(sessions) > 0
    sess = sessions[0]
    assert sess.harness == "antigravity"
    assert sess.source_format == "jsonl"
    assert sess.step_count > 0

    steps = adapter.load_steps(sess)
    assert len(steps) > 0
    assert steps[0].step_index == 0

    # Verify step filtering since_last_user_input
    last_turn_steps = adapter.load_steps(sess, since_last_user_input=True)
    assert len(last_turn_steps) > 0
    assert last_turn_steps[0].actor.role == ActorRole.USER
