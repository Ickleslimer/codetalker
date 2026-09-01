import json
from pathlib import Path

from codetalker.adapter_base import BaseAdapter
from codetalker.schema import Actor, ActorRole, NormalizedStep, TextBlock
from codetalker.server import codetalk_capabilities, codetalk_list, codetalk_read

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_steps(count: int) -> list[NormalizedStep]:
    steps: list[NormalizedStep] = []
    for i in range(count):
        role = ActorRole.USER if i % 2 == 0 else ActorRole.ASSISTANT
        steps.append(
            NormalizedStep(
                step_index=i,
                actor=Actor(role=role),
                blocks=[TextBlock(text=f"step-{i}")],
            )
        )
    return steps


def test_paginate_from_end():
    steps = _make_steps(10)
    sliced, pagination = BaseAdapter.paginate_steps(steps, from_end=True, limit=3)
    assert len(sliced) == 3
    assert sliced[0].step_index == 7
    assert sliced[-1].step_index == 9
    assert pagination.has_more_before is True
    assert pagination.total_steps_available == 10


def test_paginate_offset_from_start():
    steps = _make_steps(8)
    sliced, pagination = BaseAdapter.paginate_steps(steps, offset=2, limit=3)
    assert [s.step_index for s in sliced] == [2, 3, 4]
    assert pagination.has_more_before is True
    assert pagination.has_more_after is True
    assert pagination.next_offset == 5


def test_filter_exclude_system_and_truncate():
    steps = [
        NormalizedStep(
            step_index=0,
            actor=Actor(role=ActorRole.SYSTEM),
            blocks=[TextBlock(text="system prompt")],
        ),
        NormalizedStep(
            step_index=1,
            actor=Actor(role=ActorRole.USER),
            blocks=[TextBlock(text="hello world")],
        ),
    ]
    filtered = BaseAdapter.filter_normalized_steps(
        steps,
        exclude_actor_roles=[ActorRole.SYSTEM],
        max_step_chars=5,
    )
    assert len(filtered) == 1
    assert filtered[0].blocks[0].is_truncated is True


def test_codetalk_read_defaults_include_pagination():
    read_str = codetalk_read(
        session_id="conv-test-uuid-1234",
        harness="chatgpt",
        root_path=str(FIXTURES_DIR),
        limit=2,
    )
    data = json.loads(read_str)
    assert "pagination" in data
    assert data["pagination"]["from_end"] is True
    assert "returned_step_range" in data["session"]


def test_codetalk_capabilities_tool():
    payload = json.loads(codetalk_capabilities())
    assert "harnesses" in payload
    assert "id_guidance" in payload
    assert payload["recommended_read_defaults"]["include_raw_data"] is False


def test_codetalk_list_slim_by_default():
    payload = json.loads(codetalk_list(limit=3))
    assert "sessions" in payload
    assert "harnesses" not in payload
    assert "harness_status" in payload
