import os
from pathlib import Path
from codetalker.adapters.opencode import OpenCodeAdapter
from codetalker.schema import ActorRole, BlockType


def test_opencode_adapter_discovery_and_read():
    adapter = OpenCodeAdapter()
    sessions = adapter.discover_sessions()

    # OpenCode desktop is installed on machine
    if sessions:
        sess = sessions[0]
        assert sess.harness == "opencode"
        assert sess.source_format in ("sqlite", "jsonl", "opencode_desktop_draft")

        steps = adapter.load_steps(sess)
        assert isinstance(steps, list)
        if steps:
            assert steps[0].step_index == 0
            assert steps[0].actor.role in (ActorRole.USER, ActorRole.ASSISTANT, ActorRole.SYSTEM)
