import json
from pathlib import Path

from codetalker.adapters.antigravity import AntigravityAdapter
from codetalker.adapters.chatgpt import ChatGPTAdapter
from codetalker.adapters.freebuff import FreebuffAdapter
from codetalker.server import codetalk_branches, codetalk_diff_branches

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_dag_branch_tree_extraction():
    res_str = codetalk_branches(
        conversation_id="conv-test-uuid-1234",
        harness="chatgpt",
        root_path=str(FIXTURES_DIR),
    )
    data = json.loads(res_str)

    assert data["conversation_id"] == "conv-test-uuid-1234"
    assert data["harness"] == "chatgpt"
    assert data["branch_count"] == 2
    assert data["has_dag"] is True

    # Verify branches
    branches = data["branches"]
    assert len(branches) == 2
    main_b = next(b for b in branches if b["is_active_path"] is True)
    fork_b = next(b for b in branches if b["is_active_path"] is False)

    assert main_b["branch_id"] == "conv-test-uuid-1234"
    assert main_b["step_count"] == 4  # node-1 -> node-2 -> node-3 -> node-4
    assert fork_b["step_count"] == 2  # node-1 -> node-fork-1

    # Verify fork points
    fork_points = data["fork_points"]
    assert len(fork_points) >= 1
    fp = fork_points[0]
    assert fp["step_id"] == "node-1"
    assert fp["variant_count"] == 2


def test_diff_branches():
    res_str = codetalk_diff_branches(
        conversation_id="conv-test-uuid-1234",
        branch_a="conv-test-uuid-1234",
        branch_b="conv-test-uuid-1234__branch_node-fork-1",
        harness="chatgpt",
        root_path=str(FIXTURES_DIR),
        summary_only=False,
    )
    data = json.loads(res_str)

    assert data["conversation_id"] == "conv-test-uuid-1234"
    assert data["common_step_count"] == 1
    assert data["divergence_step_id"] == "node-1"
    assert data["divergence_step_index"] == 0

    # Common step is the initial user prompt
    common_step = data["common_steps"][0]
    assert common_step["actor"]["role"] == "user"
    assert "auth hook in React" in common_step["blocks"][0]["text"]

    # Branch A distinct steps (steps 2, 3, 4)
    assert data["branch_a_distinct_step_count"] == 3
    assert any("useAuth" in b.get("text", "") for b in data["branch_a_distinct_steps"][0]["blocks"])
    assert any("token refresh" in b.get("text", "") for b in data["branch_a_distinct_steps"][1]["blocks"])

    # Branch B distinct steps (Zustand fork)
    assert data["branch_b_distinct_step_count"] == 1
    assert any("Zustand" in b.get("text", "") for b in data["branch_b_distinct_steps"][0]["blocks"])


def test_antigravity_branch_tree_structure(tmp_path):
    # Create sample Antigravity transcript with subagents
    transcript_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>Build search feature</USER_REQUEST>",
            "step_index": 0,
        }),
        json.dumps({
            "type": "PLANNER_RESPONSE",
            "thinking": "Need to delegate to research subagent",
            "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"TypeName": "research"}]}}],
            "step_index": 1,
        }),
        json.dumps({
            "type": "GENERIC",
            "content": '{"result": {"conversationId": "subagent-uuid-5678"}}',
            "step_index": 2,
        }),
    ]
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    adapter = AntigravityAdapter()
    session = adapter._inspect_transcript_file(str(transcript_file))
    assert session is not None
    assert "subagent-uuid-5678" in session.child_session_ids
    assert session.has_dag is True

    tree = adapter.get_branch_tree(conversation_id=session.session_id, root_path=str(transcript_file))
    assert tree is not None
    assert tree.branch_count >= 1
    assert "subagent-uuid-5678" in tree.child_subagent_sessions
