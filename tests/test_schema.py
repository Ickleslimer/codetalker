from codetalker.schema import (
    Actor,
    ActorRole,
    ApprovalBlock,
    ApprovalOutcome,
    AttachmentBlock,
    BlockType,
    BranchInfo,
    CodeDiffBlock,
    DiffStatus,
    NormalizedSession,
    NormalizedStep,
    SystemEventBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def test_schema_blocks_and_serialization():
    blocks = [
        TextBlock(text="Hello world"),
        ThinkingBlock(text="Reasoning step", has_signature=True),
        ToolCallBlock(tool_call_id="call_1", tool_name="bash", tool_args={"cmd": "ls"}),
        ToolResultBlock(tool_call_id="call_1", tool_name="bash", content="file1.txt\nfile2.txt"),
        CodeDiffBlock(file_uri="file:///main.py", diff="+ print(1)", status=DiffStatus.APPLIED),
        AttachmentBlock(attachment_type="image", url="https://example.com/pic.png"),
        ApprovalBlock(outcome=ApprovalOutcome.APPROVED, action_description="Run command"),
        SystemEventBlock(event_name="checkpoint", detail="Session saved"),
    ]

    step = NormalizedStep(
        step_index=0,
        timestamp="2026-08-22T12:00:00Z",
        actor=Actor(role=ActorRole.ASSISTANT, model="gpt-4o"),
        blocks=blocks,
        branch=BranchInfo(step_id="step-1", parent_step_id=None),
        raw_data={"test": 123},
    )

    data = step.model_dump(mode="json")
    assert data["step_index"] == 0
    assert len(data["blocks"]) == 8
    assert data["blocks"][0]["type"] == "text"
    assert data["blocks"][1]["type"] == "thinking"
    assert data["blocks"][1]["has_signature"] is True

    # Round trip validation
    step_restored = NormalizedStep.model_validate(data)
    assert step_restored.step_index == 0
    assert isinstance(step_restored.blocks[0], TextBlock)
    assert isinstance(step_restored.blocks[1], ThinkingBlock)
    assert isinstance(step_restored.blocks[2], ToolCallBlock)
    assert isinstance(step_restored.blocks[4], CodeDiffBlock)


def test_session_serialization():
    session = NormalizedSession(
        session_id="conv-123",
        harness="chatgpt",
        display_name="Test Session",
        conversation_id="conv-123",
        branch_root_step_id=None,
        branch_label="Main Thread",
        started_at="2026-08-22T12:00:00Z",
        last_activity="2026-08-22T12:30:00Z",
        step_count=5,
        user_turn_count=2,
        assistant_turn_count=3,
        source_path="C:/tmp/test.json",
        source_format="export_json",
        has_dag=True,
    )

    data = session.model_dump(mode="json")
    assert data["session_id"] == "conv-123"
    assert data["has_dag"] is True

    restored = NormalizedSession.model_validate(data)
    assert restored.harness == "chatgpt"
    assert restored.branch_label == "Main Thread"
