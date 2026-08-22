import json
import tempfile
from pathlib import Path
from codetalker.adapters.claude import ClaudeCodeAdapter
from codetalker.schema import ActorRole, BlockType


def test_claude_adapter_fixture():
    # Synthetic Claude Code session JSONL
    sample_jsonl = """{"type":"user","timestamp":"2026-08-20T14:30:00.000Z","message":{"role":"user","content":"Optimize the database index"}}
{"type":"assistant","timestamp":"2026-08-20T14:30:05.000Z","message":{"role":"assistant","model":"claude-3-7-sonnet","content":[{"type":"thinking","thinking":"Let me check the schema...","signature":"sig123"},{"type":"text","text":"I will create an index."},{"type":"tool_use","id":"tool_1","name":"bash","input":{"command":"psql -c 'CREATE INDEX...'"}}]}}
{"type":"tool_result","timestamp":"2026-08-20T14:30:08.000Z","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool_1","content":"CREATE INDEX successful"}]}}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(sample_jsonl)
        tmp_path = f.name

    try:
        adapter = ClaudeCodeAdapter()
        sessions = adapter.discover_sessions(root_path=tmp_path)
        assert len(sessions) == 1
        sess = sessions[0]
        assert sess.harness == "claude"
        assert sess.display_name == "Optimize the database index"
        assert sess.model == "claude-3-7-sonnet"

        steps = adapter.load_steps(sess)
        assert len(steps) == 3
        # Step 0: user
        assert steps[0].actor.role == ActorRole.USER
        assert steps[0].blocks[0].text == "Optimize the database index"

        # Step 1: assistant with thinking and tool
        assert steps[1].actor.role == ActorRole.ASSISTANT
        block_types = [b.type for b in steps[1].blocks]
        assert BlockType.THINKING in block_types
        assert BlockType.TEXT in block_types
        assert BlockType.TOOL_CALL in block_types

        # Step 2: tool result
        assert steps[2].actor.role in (ActorRole.TOOL, ActorRole.SYSTEM, ActorRole.USER)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
