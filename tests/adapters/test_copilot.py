import json
import tempfile
from pathlib import Path
from codetalker.adapters.copilot import GitHubCopilotAdapter
from codetalker.schema import ActorRole, BlockType


def test_copilot_adapter_fixture():
    sample_copilot_jsonl = """{"type":"request","timestamp":1785020000000,"message":{"text":"How do I configure logging in FastAPI?"},"usedReferences":[{"reference":{"value":"file:///app/main.py"}}]}
{"type":"response","timestamp":1785020005000,"model":"gpt-4o","response":[{"value":"Use standard Python `logging` with `uvicorn`."},{"name":"read_file","parameters":{"path":"app/main.py"}}]}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(sample_copilot_jsonl)
        tmp_path = f.name

    try:
        adapter = GitHubCopilotAdapter()
        sessions = adapter.discover_sessions(root_path=tmp_path)
        assert len(sessions) == 1
        sess = sessions[0]
        assert sess.harness == "copilot"
        assert "How do I configure logging" in sess.display_name

        steps = adapter.load_steps(sess)
        assert len(steps) == 2
        # User step with attachment
        assert steps[0].actor.role == ActorRole.USER
        assert steps[0].blocks[0].text == "How do I configure logging in FastAPI?"
        assert any(b.type == BlockType.ATTACHMENT for b in steps[0].blocks)

        # Assistant step with text and tool
        assert steps[1].actor.role == ActorRole.ASSISTANT
        block_types = [b.type for b in steps[1].blocks]
        assert BlockType.TEXT in block_types
        assert BlockType.TOOL_CALL in block_types
    finally:
        Path(tmp_path).unlink(missing_ok=True)
