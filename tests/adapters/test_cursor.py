import os
from pathlib import Path
from codetalker.adapters.cursor import CursorAdapter
from codetalker.schema import ActorRole, TextBlock


def test_cursor_adapter_discovery_and_read():
    adapter = CursorAdapter()
    sessions = adapter.discover_sessions()

    # If Cursor is installed on the machine, sessions should be discovered
    if sessions:
        sess = sessions[0]
        assert sess.harness == "cursor"
        assert sess.source_format == "sqlite"

        steps = adapter.load_steps(sess)
        assert isinstance(steps, list)
        if steps:
            assert steps[0].step_index == 0
            assert steps[0].actor.role in (ActorRole.USER, ActorRole.ASSISTANT, ActorRole.SYSTEM)
