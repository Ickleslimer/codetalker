import os
from pathlib import Path
from codetalker.adapters.windsurf import WindsurfAdapter, _decode_protobuf_wire
from codetalker.schema import ActorRole, TextBlock


def test_protobuf_decoder_wire():
    # Simple encoded protobuf bytes: field 1 (string "hello"), field 2 (varint 42)
    # tag 1: (1 << 3) | 2 = 0x0A, len 5, "hello"
    # tag 2: (2 << 3) | 0 = 0x10, val 42
    raw = b"\x0a\x05hello\x10\x2a"
    fields = _decode_protobuf_wire(raw)
    assert len(fields) == 2
    assert fields[0][0] == 1
    assert fields[0][1] == "string"
    assert fields[0][2] == "hello"
    assert fields[1][0] == 2
    assert fields[1][1] == "varint"
    assert fields[1][2] == 42


def test_windsurf_adapter_discovery_and_read():
    adapter = WindsurfAdapter()
    sessions = adapter.discover_sessions()
    
    # If .codeium is present on machine, sessions will be discovered
    if sessions:
        sess = sessions[0]
        assert sess.harness == "windsurf"
        assert sess.source_format in ("protobuf", "sqlite")
        
        steps = adapter.load_steps(sess)
        assert isinstance(steps, list)
        if steps:
            assert steps[0].step_index == 0
            assert steps[0].actor.role in (ActorRole.USER, ActorRole.ASSISTANT, ActorRole.SYSTEM)
