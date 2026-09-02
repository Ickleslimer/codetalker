import json
from pathlib import Path

import codetalker.adapters  # noqa: F401
from codetalker.schema import (
    Actor,
    ActorRole,
    NormalizedSession,
    NormalizedStep,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from codetalker.search import SearchOptions, search_sessions
from codetalker.server import codetalk_search

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_session(session_id: str = "search-test") -> NormalizedSession:
    return NormalizedSession(
        session_id=session_id,
        harness="chatgpt",
        display_name="Build a React Auth Hook",
        conversation_id="conv-test",
        started_at="2026-08-25T00:00:00+00:00",
        last_activity="2026-08-30T00:00:00+00:00",
        source_path=str(FIXTURES_DIR / "chatgpt_dag_export.json"),
        source_format="json",
    )


class RecordingAdapter:
    harness_name = "chatgpt"

    def __init__(self, sessions: list[NormalizedSession], steps_by_id: dict[str, list[NormalizedStep]]):
        self.sessions = sessions
        self.steps_by_id = steps_by_id
        self.load_calls = 0

    def discover_sessions(self, root_path: str | None = None):
        return list(self.sessions)

    def load_steps(self, session: NormalizedSession, **kwargs):
        self.load_calls += 1
        steps = list(self.steps_by_id.get(session.session_id, []))
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")
        from_end = kwargs.get("from_end", False)
        if from_end and limit:
            return steps[-limit:]
        if limit is not None:
            return steps[offset : offset + limit]
        return steps[offset:]


def test_full_scope_finds_early_step_tail_scope_misses(monkeypatch):
    steps = [
        NormalizedStep(
            step_index=0,
            actor=Actor(role=ActorRole.USER),
            blocks=[TextBlock(text="please run codetalk_read on the session")],
        )
    ]
    steps.extend(
        NormalizedStep(
            step_index=i,
            actor=Actor(role=ActorRole.ASSISTANT),
            blocks=[TextBlock(text=f"filler step {i}")],
        )
        for i in range(1, 80)
    )
    session = _make_session()
    adapter = RecordingAdapter([session], {session.session_id: steps})

    from codetalker import registry as reg_mod

    monkeypatch.setattr(reg_mod.registry, "get", lambda h: adapter if h == "chatgpt" else None)

    tail_hits = search_sessions(
        SearchOptions(
            query="codetalk_read",
            harness="chatgpt",
            search_scope="tail",
            max_sessions_to_search=1,
            limit=10,
        )
    )
    full_hits = search_sessions(
        SearchOptions(
            query="codetalk_read",
            harness="chatgpt",
            search_scope="full",
            max_sessions_to_search=1,
            limit=10,
        )
    )

    assert len(tail_hits) == 0
    assert len(full_hits) == 1
    assert full_hits[0]["match_type"] == "content"
    assert full_hits[0]["matched_term"] == "codetalk_read"


def test_multiple_hits_per_session(monkeypatch):
    steps = [
        NormalizedStep(
            step_index=0,
            actor=Actor(role=ActorRole.USER),
            blocks=[TextBlock(text="use codetalk_list first")],
        ),
        NormalizedStep(
            step_index=1,
            actor=Actor(role=ActorRole.ASSISTANT),
            blocks=[TextBlock(text="then codetalk_read the session")],
        ),
    ]
    session = _make_session("multi-hit")
    adapter = RecordingAdapter([session], {session.session_id: steps})

    from codetalker import registry as reg_mod

    monkeypatch.setattr(reg_mod.registry, "get", lambda h: adapter if h == "chatgpt" else None)

    hits = search_sessions(
        SearchOptions(
            query="codetalk",
            harness="chatgpt",
            search_scope="full",
            max_hits_per_session=0,
            max_sessions_to_search=1,
            limit=0,
        )
    )
    assert len(hits) == 2


def test_tool_name_and_error_pairing(monkeypatch):
    steps = [
        NormalizedStep(
            step_index=0,
            actor=Actor(role=ActorRole.ASSISTANT),
            blocks=[
                ToolCallBlock(
                    tool_call_id="call-1",
                    tool_name="codetalk_read",
                    tool_args={"session_id": "missing"},
                )
            ],
        ),
        NormalizedStep(
            step_index=1,
            actor=Actor(role=ActorRole.TOOL),
            blocks=[
                ToolResultBlock(
                    tool_call_id="call-1",
                    tool_name="codetalk_read",
                    content="Session 'missing' not found across all harnesses.",
                    is_error=True,
                )
            ],
        ),
    ]
    session = _make_session("tool-hit")
    adapter = RecordingAdapter([session], {session.session_id: steps})

    from codetalker import registry as reg_mod

    monkeypatch.setattr(reg_mod.registry, "get", lambda h: adapter if h == "chatgpt" else None)

    hits = search_sessions(
        SearchOptions(
            query="codetalk_read",
            harness="chatgpt",
            search_scope="full",
            max_sessions_to_search=1,
            max_hits_per_session=0,
            limit=0,
            match_tool_names=True,
        )
    )
    tool_hits = [h for h in hits if h["match_type"] == "tool_call"]
    assert len(tool_hits) == 1
    assert tool_hits[0]["tool_result_is_error"] is True
    assert "not found" in tool_hits[0]["tool_result_preview"]


def test_unlimited_sessions_searches_beyond_thirty(monkeypatch):
    sessions = [
        NormalizedSession(
            session_id=f"sess-{i}",
            harness="chatgpt",
            display_name=f"Session {i}",
            conversation_id=f"sess-{i}",
            started_at="2026-08-25T00:00:00+00:00",
            last_activity=f"2026-08-{25 + i % 5:02d}T00:00:00+00:00",
            source_path="x",
            source_format="json",
        )
        for i in range(40)
    ]
    steps = [
        NormalizedStep(
            step_index=0,
            actor=Actor(role=ActorRole.USER),
            blocks=[TextBlock(text="codetalker mention")],
        )
    ]
    steps_by_id = {s.session_id: steps for s in sessions}
    adapter = RecordingAdapter(sessions, steps_by_id)

    from codetalker import registry as reg_mod

    monkeypatch.setattr(reg_mod.registry, "get", lambda h: adapter if h == "chatgpt" else None)

    limited = search_sessions(
        SearchOptions(
            query="codetalker",
            harness="chatgpt",
            search_scope="full",
            max_sessions_to_search=30,
            max_hits_per_session=1,
            limit=0,
        )
    )
    unlimited = search_sessions(
        SearchOptions(
            query="codetalker",
            harness="chatgpt",
            search_scope="full",
            max_sessions_to_search=0,
            max_hits_per_session=1,
            limit=0,
        )
    )
    assert len(limited) == 30
    assert len(unlimited) == 40
    assert adapter.load_calls >= 40


def test_codetalk_search_mcp_exposes_new_params():
    payload = json.loads(
        codetalk_search(
            query="React",
            harness="chatgpt",
            root_path=str(FIXTURES_DIR),
            limit=5,
            search_scope="full",
            max_hits_per_session=2,
        )
    )
    assert payload["search_scope"] == "full"
    assert "results" in payload
    assert len(payload["results"]) >= 1
