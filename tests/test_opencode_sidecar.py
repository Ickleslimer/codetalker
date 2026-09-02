import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from codetalker.adapters.opencode import OpenCodeAdapter
from codetalker.adapters.opencode_sidecar import (
    discover_sessions_from_db,
    fetch_sidecar_messages,
    load_steps_from_sidecar,
)
from codetalker.schema import ActorRole, BlockType, NormalizedSession


def _write_sidecar_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            workspace_id TEXT,
            parent_id TEXT,
            slug TEXT,
            directory TEXT,
            path TEXT,
            title TEXT,
            version TEXT,
            share_url TEXT,
            summary_additions INTEGER,
            summary_deletions INTEGER,
            summary_files INTEGER,
            summary_diffs INTEGER,
            metadata TEXT,
            cost REAL,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_reasoning INTEGER,
            tokens_cache_read INTEGER,
            tokens_cache_write INTEGER,
            revert TEXT,
            permission TEXT,
            agent TEXT,
            model TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            time_compacting INTEGER,
            time_archived INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        )
        """
    )

    sid = "ses_sidecar123"
    conn.execute(
        """
        INSERT INTO session (
            id, title, directory, model, time_created, time_updated
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            sid,
            "Codetalker MCP access",
            "D:/path/to/project",
            json.dumps({"providerID": "opencode", "modelID": "test-model"}),
            1787878241772,
            1787881333596,
        ),
    )

    user_msg = {
        "role": "user",
        "time": {"created": 1787878241772},
        "agent": "build",
    }
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_user1", sid, 1787878241772, 1787878241772, json.dumps(user_msg)),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_user1",
            "msg_user1",
            sid,
            1787878241772,
            1787878241772,
            json.dumps({"type": "text", "text": "Do you have access to the Codetalker MCP?"}),
        ),
    )

    assistant_msg = {
        "role": "assistant",
        "time": {"created": 1787878250000},
        "model": {"providerID": "opencode", "modelID": "test-model"},
    }
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_asst1", sid, 1787878250000, 1787878250000, json.dumps(assistant_msg)),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_reason1",
            "msg_asst1",
            sid,
            1787878250000,
            1787878250000,
            json.dumps({"type": "reasoning", "text": "Checking MCP tools."}),
        ),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_text1",
            "msg_asst1",
            sid,
            1787878251000,
            1787878251000,
            json.dumps({"type": "text", "text": "Yes, Codetalker MCP is available."}),
        ),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_tool1",
            "msg_asst1",
            sid,
            1787878252000,
            1787878252000,
            json.dumps(
                {
                    "type": "tool",
                    "tool": "codetalker_codetalk_list",
                    "callID": "call-123",
                    "state": {
                        "status": "completed",
                        "input": {"harness": "antigravity"},
                        "output": '{"count": 1}',
                    },
                }
            ),
        ),
    )

    conn.commit()
    conn.close()
    return db_path


def test_discover_sessions_from_sidecar_db(tmp_path):
    db_path = _write_sidecar_db(tmp_path)
    sessions = discover_sessions_from_db(str(db_path))
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess.session_id == "ses_sidecar123"
    assert sess.display_name == "Codetalker MCP access"
    assert sess.source_format == "opencode_sidecar"
    assert sess.user_turn_count == 1
    assert sess.assistant_turn_count == 1


def test_load_steps_from_sidecar_db(tmp_path):
    db_path = _write_sidecar_db(tmp_path)
    session = discover_sessions_from_db(str(db_path))[0]
    steps = load_steps_from_sidecar(session)
    assert len(steps) == 4
    assert steps[0].actor.role == ActorRole.USER
    assert steps[0].blocks[0].type == BlockType.TEXT
    assert "Codetalker MCP" in steps[0].blocks[0].text
    assert any(s.blocks[0].type == BlockType.THINKING for s in steps if s.actor.role == ActorRole.ASSISTANT)
    tool_call = next(
        s for s in steps for b in s.blocks if b.type == BlockType.TOOL_CALL
    )
    tool_result = next(
        s for s in steps for b in s.blocks if b.type == BlockType.TOOL_RESULT
    )
    call_block = next(b for b in tool_call.blocks if b.type == BlockType.TOOL_CALL)
    result_block = next(b for b in tool_result.blocks if b.type == BlockType.TOOL_RESULT)
    assert call_block.tool_name == "codetalker_codetalk_list"
    assert result_block.content == '{"count": 1}'


def test_fetch_sidecar_messages_uses_http_payload():
    payload = [
        {
            "info": {"role": "user", "time": {"created": 1787878241772}},
            "parts": [{"type": "text", "text": "Hello from HTTP"}],
        }
    ]
    with patch(
        "codetalker.adapters.opencode_sidecar._http_get_json",
        return_value=payload,
    ):
        messages = fetch_sidecar_messages("ses_http", base_url="http://127.0.0.1:4096")
    assert messages == payload


def test_adapter_prefers_sidecar_db_over_desktop_draft(tmp_path):
    desktop = tmp_path / "ai.opencode.desktop"
    desktop.mkdir()
    conn = sqlite3.connect(desktop / "drafts.sqlite")
    conn.execute("CREATE TABLE document (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO document (key, value) VALUES (?, ?)",
        (
            "opencode.workspace.RDpcQWdhcnRo.1qa9m3f.dat:session:ses_sidecar123:prompt",
            json.dumps({"prompt": [{"content": "draft-only prompt"}], "model": {"modelID": "draft-model"}}),
        ),
    )
    conn.commit()
    conn.close()

    sidecar_db = _write_sidecar_db(tmp_path)
    adapter = OpenCodeAdapter()

    with patch(
        "codetalker.adapters.opencode.default_opencode_db_path",
        return_value=str(sidecar_db),
    ):
        sessions = adapter.discover_sessions(root_path=str(desktop / "drafts.sqlite"))

    by_id = {s.session_id: s for s in sessions}
    assert by_id["ses_sidecar123"].source_format == "opencode_sidecar"
    assert by_id["ses_sidecar123"].display_name == "Codetalker MCP access"

    steps = adapter.load_steps(by_id["ses_sidecar123"])
    assert len(steps) >= 4
    assert any("Codetalker MCP is available" in b.text for s in steps for b in s.blocks if hasattr(b, "text"))
