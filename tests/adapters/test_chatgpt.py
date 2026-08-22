import os
from pathlib import Path

from codetalker.adapters.chatgpt import ChatGPTAdapter
from codetalker.schema import ActorRole, BlockType, TextBlock, ThinkingBlock, ToolCallBlock


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def test_discover_dag_export():
    adapter = ChatGPTAdapter()
    dag_fixture = str(FIXTURES_DIR / "chatgpt_dag_export.json")
    sessions = adapter.discover_sessions(root_path=dag_fixture)

    # There should be 2 branches: the main trunk (leaf node-4) and the fork (leaf node-fork-1)
    assert len(sessions) == 2

    trunk = next(s for s in sessions if s.branch_root_step_id is None)
    fork = next(s for s in sessions if s.branch_root_step_id is not None)

    assert trunk.session_id == "conv-test-uuid-1234"
    assert trunk.display_name == "Build a React Auth Hook"
    assert trunk.step_count == 4  # node-1 -> node-2 -> node-3 -> node-4
    assert trunk.user_turn_count == 2
    assert trunk.assistant_turn_count == 2
    assert trunk.has_dag is True

    assert fork.session_id == "conv-test-uuid-1234__branch_node-fork-1"
    assert "Branch" in (fork.display_name or "")
    assert fork.branch_root_step_id == "node-1"
    assert fork.step_count == 2  # node-1 -> node-fork-1
    assert fork.user_turn_count == 1
    assert fork.assistant_turn_count == 1


def test_load_steps_dag_export_trunk():
    adapter = ChatGPTAdapter()
    dag_fixture = str(FIXTURES_DIR / "chatgpt_dag_export.json")
    sessions = adapter.discover_sessions(root_path=dag_fixture)
    trunk = next(s for s in sessions if s.branch_root_step_id is None)

    steps = adapter.load_steps(trunk)
    assert len(steps) == 4

    # Step 0: User prompt
    assert steps[0].actor.role == ActorRole.USER
    assert any(isinstance(b, TextBlock) and "auth hook" in b.text for b in steps[0].blocks)

    # Step 1: Assistant with thinking
    assert steps[1].actor.role == ActorRole.ASSISTANT
    assert steps[1].actor.model == "gpt-4o"
    assert any(isinstance(b, ThinkingBlock) and "React context" in b.text for b in steps[1].blocks)
    assert any(isinstance(b, TextBlock) and "useAuth" in b.text for b in steps[1].blocks)

    # Test filtering since_last_user_input
    sliced_steps = adapter.load_steps(trunk, since_last_user_input=True)
    # The last user turn is Step 2 ("Can you add token refresh logic?")
    assert len(sliced_steps) == 2
    assert sliced_steps[0].actor.role == ActorRole.USER
    assert "token refresh" in sliced_steps[0].blocks[0].text
    assert sliced_steps[1].actor.role == ActorRole.ASSISTANT

    # Test opt-out of thinking blocks
    no_thinking_steps = adapter.load_steps(trunk, include_thinking=False)
    for s in no_thinking_steps:
        assert not any(isinstance(b, ThinkingBlock) for b in s.blocks)


def test_discover_and_load_codex_rollout():
    adapter = ChatGPTAdapter()
    rollout_fixture = str(FIXTURES_DIR / "codex_sample_rollout.jsonl")
    sessions = adapter.discover_sessions(root_path=rollout_fixture)

    assert len(sessions) == 1
    sess = sessions[0]
    assert sess.source_format == "codex_rollout"
    assert sess.working_directory == "C:\\Work\\project"
    assert sess.model == "gpt-5.3-codex"

    steps = adapter.load_steps(sess)
    assert len(steps) >= 5

    # Check tool calls
    tool_steps = [s for s in steps if any(isinstance(b, ToolCallBlock) for b in s.blocks)]
    assert len(tool_steps) >= 1
    call_block = next(b for b in tool_steps[0].blocks if isinstance(b, ToolCallBlock))
    assert call_block.tool_name == "check_health"
    assert call_block.tool_args == {"service": "auth"}

    # Test filtering since_last_user_input on rollout
    # Last user turn in fixture is "Now restart the auth service"
    last_turn_steps = adapter.load_steps(sess, since_last_user_input=True)
    assert len(last_turn_steps) == 2
    assert last_turn_steps[0].actor.role == ActorRole.USER
    assert "restart the auth service" in last_turn_steps[0].blocks[0].text
    assert last_turn_steps[1].actor.role == ActorRole.ASSISTANT
