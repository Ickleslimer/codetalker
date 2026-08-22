import os
from pathlib import Path
from codetalker.adapters.freebuff import FreebuffAdapter
from codetalker.schema import ActorRole, BlockType


def test_freebuff_adapter_discovery_and_read():
    adapter = FreebuffAdapter()
    sessions = adapter.discover_sessions()

    # Freebuff is installed on machine, so sessions in Agartha/Skyrim will be discovered
    if sessions:
        sess = sessions[0]
        assert sess.harness == "freebuff"
        assert sess.source_format == "sqlite"
        assert sess.step_count > 0

        steps = adapter.load_steps(sess)
        assert len(steps) > 0
        assert steps[0].step_index == 0
        assert steps[0].actor.role in (ActorRole.USER, ActorRole.ASSISTANT, ActorRole.SYSTEM)
