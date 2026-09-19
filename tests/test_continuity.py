"""v0.3 continuity layer: mechanical fresh-session detection, per-client
instruction tailoring, and the continue-token anchor.

All transcript fixtures are synthetic NormalizedStep objects — no live data.
"""
from __future__ import annotations

import json

import pytest

from codetalker.agent_guidance import (
    SERVER_INSTRUCTIONS,
    SERVER_INSTRUCTIONS_FALLBACK,
    select_instructions,
)
from codetalker.continuity import (
    RESTART_MARKERS,
    ContinueToken,
    detect_session_fresh,
    emit_continue_token,
    extract_step_text,
    instruction_block,
    parse_continue_token,
    verify_continue_token,
)
from codetalker.schema import Actor, ActorRole, NormalizedStep, TextBlock


def _step(idx: int, role: ActorRole, text: str) -> NormalizedStep:
    return NormalizedStep(step_index=idx, actor=Actor(role=role), blocks=[TextBlock(text=text)])


# ─── fresh-session detection ─────────────────────────────────────────────────


def test_restart_marker_in_user_step_flags_fresh():
    steps = [
        _step(0, ActorRole.USER, "do the thing"),
        _step(1, ActorRole.ASSISTANT, "done"),
        _step(
            2,
            ActorRole.USER,
            "Continue the request.\n\n<since_your_last_turn>\nFreebuff restarted…\n</since_your_last_turn>",
        ),
    ]
    report = detect_session_fresh(steps)
    assert report.is_fresh is True
    assert report.signal == "restart_or_failure_marker"
    assert report.marker_step_index == 2


def test_failed_turn_marker_flags_fresh():
    steps = [
        _step(0, ActorRole.USER, "go"),
        _step(
            1,
            ActorRole.SYSTEM,
            "<system>The session ended before this response completed.</system>",
        ),
    ]
    assert detect_session_fresh(steps).is_fresh is True


def test_quoted_marker_in_assistant_step_does_not_trip():
    steps = [
        _step(1, ActorRole.ASSISTANT, "The marker <failed_turn> is noted in my plan."),
        _step(2, ActorRole.USER, "now do step 2"),
    ]
    report = detect_session_fresh(steps)
    assert report.is_fresh is False
    assert report.signal == "none"


def test_user_typing_marker_is_accepted_false_positive():
    # Freebuff injects wipe notices into USER-role steps, so a user literally
    # typing a marker is indistinguishable from a real wipe. Accepted: the
    # cost of this false positive is one cheap recovery call, never a wrong
    # answer.
    steps = [
        _step(0, ActorRole.USER, "remember the string <failed_turn> for later"),
        _step(1, ActorRole.USER, "now do step 2"),
    ]
    assert detect_session_fresh(steps).is_fresh is True


def test_healthy_tail_is_not_fresh():
    steps = [
        _step(0, ActorRole.USER, "hello"),
        _step(1, ActorRole.ASSISTANT, "Hi!"),
        _step(2, ActorRole.USER, "now add tests"),
    ]
    report = detect_session_fresh(steps)
    assert report.is_fresh is False
    assert report.steps_scanned == 3


def test_window_respected():
    steps = [_step(i, ActorRole.USER, f"m{i}") for i in range(200)]
    steps.append(_step(200, ActorRole.USER, "again " + RESTART_MARKERS[0]))
    # Marker inside the default 60-step tail (steps 141..200) is seen.
    assert detect_session_fresh(steps).is_fresh is True
    # A marker buried mid-history (step 100) is outside the tail window.
    mid = [_step(i, ActorRole.USER, f"m{i}") for i in range(200)]
    mid[100] = _step(100, ActorRole.USER, "old " + RESTART_MARKERS[0])
    assert detect_session_fresh(mid, scan_window=5).is_fresh is False
    assert detect_session_fresh(mid, scan_window=120).is_fresh is True


def test_extract_step_text_ignores_non_text_blocks():
    step = _step(0, ActorRole.USER, "hello")
    step.blocks.append(TextBlock(text="world", is_truncated=True))
    assert "hello" in extract_step_text(step)
    assert "world" in extract_step_text(step)


# ─── instruction tailoring ───────────────────────────────────────────────────


def test_freebuff_gets_full_mandate():
    assert select_instructions("freebuff") is SERVER_INSTRUCTIONS
    assert "codetalker-v3-continue" in SERVER_INSTRUCTIONS


def test_other_harnesses_get_fallback():
    assert select_instructions("Claude Desktop") is SERVER_INSTRUCTIONS_FALLBACK
    assert select_instructions("cursor") is SERVER_INSTRUCTIONS_FALLBACK
    assert select_instructions(None) is SERVER_INSTRUCTIONS_FALLBACK
    assert len(SERVER_INSTRUCTIONS_FALLBACK) < len(SERVER_INSTRUCTIONS)


def test_instruction_block_reports_state():
    steps = [_step(0, ActorRole.USER, "<failed_turn> x")]
    assert "RESTART" in instruction_block(detect_session_fresh(steps))
    steps_ok = [_step(0, ActorRole.USER, "hi")]
    assert "No restart markers" in instruction_block(detect_session_fresh(steps_ok))


# ─── continue token: emit / parse round-trip ─────────────────────────────────


def test_token_round_trip():
    line = emit_continue_token(
        session_id="sess-1",
        working_directory=r"C:\Work\project",
        last_user_step_index=12,
        total_steps=40,
        last_user_text="Now restart the auth service",
    )
    assert line.startswith("codetalker-v3-continue ")
    token = parse_continue_token(line)
    assert token is not None
    assert token.session_id == "sess-1"
    assert token.last_user_step_index == 12
    assert token.total_steps == 40
    assert "auth service" in token.last_user_text


def test_token_embedded_in_long_text_is_found():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=3,
        total_steps=9,
        last_user_text="run the tests",
    )
    text = "Blah blah\n" + line + "\nMore prose after."
    token = parse_continue_token(text)
    assert token is not None and token.last_user_step_index == 3


def test_token_with_newlines_in_user_text_round_trips():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=1,
        total_steps=4,
        last_user_text="first line\nsecond line",
    )
    token = parse_continue_token(line)
    assert token is not None
    assert token.last_user_text == "first line second line"


def test_edited_token_fails_signature():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=5,
        total_steps=10,
        last_user_text="honest anchor",
    )
    tampered = line.replace("honest anchor", "forged anchor")
    assert parse_continue_token(tampered) is None


def test_truncated_token_returns_none():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=5,
        total_steps=10,
        last_user_text="anchor",
    )
    assert parse_continue_token(line[: len(line) // 2]) is None


def test_absent_token_returns_none():
    assert parse_continue_token("no token here at all") is None
    assert parse_continue_token("") is None


# ─── continue token: verification against steps ──────────────────────────────


def _anchored_steps(anchor_text: str, extra: int = 3):
    steps = [
        _step(0, ActorRole.USER, "start work"),
        _step(1, ActorRole.ASSISTANT, "working..."),
        _step(2, ActorRole.USER, anchor_text),
    ]
    for i in range(extra):
        steps.append(_step(3 + i, ActorRole.ASSISTANT, f"progress {i}"))
    return steps


def test_verify_matching_anchor():
    line = emit_continue_token(
        session_id="sess-9",
        working_directory="w",
        last_user_step_index=2,
        total_steps=6,
        last_user_text="the exact user instruction",
    )
    token = parse_continue_token(line)
    check = verify_continue_token(token, _anchored_steps("the exact user instruction"))
    assert check.matches is True


def test_verify_missing_anchor_text():
    line = emit_continue_token(
        session_id="sess-9",
        working_directory="w",
        last_user_step_index=2,
        total_steps=6,
        last_user_text="text that exists only in a stale context window",
    )
    token = parse_continue_token(line)
    check = verify_continue_token(token, _anchored_steps("different user text entirely"))
    assert check.matches is False
    assert "not found" in check.reason


def test_verify_shrunk_transcript_fails():
    line = emit_continue_token(
        session_id="sess-9",
        working_directory="w",
        last_user_step_index=2,
        total_steps=50,
        last_user_text="anchor words",
    )
    token = parse_continue_token(line)
    check = verify_continue_token(
        token, _anchored_steps("anchor words"), total_steps=6
    )
    assert check.matches is False
    assert "6 steps" in check.reason


def test_verify_session_mismatch():
    line = emit_continue_token(
        session_id="sess-A",
        working_directory="w",
        last_user_step_index=2,
        total_steps=6,
        last_user_text="anchor words",
    )
    token = parse_continue_token(line)
    check = verify_continue_token(
        token, _anchored_steps("anchor words"), session_id="sess-B"
    )
    assert check.matches is False


def test_verify_unknown_continuity_mode():
    token = ContinueToken(continuity_mode="codetalker-v1")
    check = verify_continue_token(token, [])
    assert check.matches is False


def test_verify_no_token():
    check = verify_continue_token(None, _anchored_steps("anything"))
    assert check.matches is False
    assert check.reason == "no token claimed"


def test_verify_uses_total_steps_override():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=2,
        total_steps=30,
        last_user_text="anchor words",
    )
    token = parse_continue_token(line)
    # Window of 6 steps, but the whole session has 30 — the structural claim
    # must be checked against the whole-session count.
    check = verify_continue_token(
        token,
        _anchored_steps("anchor words"),
        total_steps=30,
    )
    assert check.matches is True


# ─── instruction-block helper smoke ──────────────────────────────────────────


def test_freshness_payload_shape():
    steps = [
        _step(0, ActorRole.USER, "hello"),
        _step(1, ActorRole.USER, "retry " + RESTART_MARKERS[1]),
    ]
    payload = detect_session_fresh(steps).to_payload()
    assert payload == {
        "is_fresh": True,
        "signal": "restart_or_failure_marker",
        "marker_step_index": 1,
        "steps_scanned": 2,
    }


def test_token_json_body_is_compact_single_line():
    line = emit_continue_token(
        session_id="s",
        working_directory="w",
        last_user_step_index=1,
        total_steps=2,
        last_user_text="x",
    )
    body = json.loads(line.split(" ", 1)[1])
    assert body["session_id"] == "s"
    assert "\n" not in line


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
