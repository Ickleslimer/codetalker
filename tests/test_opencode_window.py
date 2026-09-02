import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from codetalker.adapters.opencode import OpenCodeAdapter


def _write_desktop_fixture(tmp_path: Path) -> Path:
    desktop = tmp_path / "ai.opencode.desktop"
    desktop.mkdir(parents=True)

    conn = sqlite3.connect(desktop / "drafts.sqlite")
    conn.execute("CREATE TABLE document (key TEXT PRIMARY KEY, value TEXT)")
    rows = [
        (
            "opencode.workspace.RDpcQWdhcnRo.1qa9m3f.dat:session:ses_empty123:prompt",
            json.dumps(
                {
                    "prompt": [{"type": "text", "content": "", "start": 0, "end": 0}],
                    "model": {"providerID": "opencode", "modelID": "test-model"},
                }
            ),
        ),
        (
            "opencode.workspace.RDpcQWdhcnRo.1qa9m3f.dat:session:ses_named456:prompt",
            json.dumps(
                {
                    "prompt": [{"type": "text", "content": "Build modpack goals", "start": 0, "end": 18}],
                    "model": {"providerID": "opencode", "modelID": "test-model"},
                }
            ),
        ),
        (
            "opencode.global.dat:prompt-history",
            json.dumps(
                {
                    "entries": [
                        {"prompt": [{"content": "Do you have access to the Codetalker MCP?"}]}
                    ]
                }
            ),
        ),
    ]
    conn.executemany("INSERT INTO document (key, value) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()

    window = {
        "tabs.info": json.dumps(
            {
                "sidecar\\n/server/c2lkZWNhcg/session/ses_empty123": {
                    "title": "Codetalker MCP access",
                    "directory": "D:\\\\Agartha Modpack",
                },
                "sidecar\\n/server/c2lkZWNhcg/session/ses_named456": {
                    "title": "Minecraft modpack build goals",
                    "directory": "D:\\\\Agartha Modpack",
                },
            }
        )
    }
    (desktop / "opencode.window.test.dat").write_text(json.dumps(window), encoding="utf-8")
    return desktop / "drafts.sqlite"


def test_opencode_discovers_empty_sidecar_session_by_window_title(tmp_path):
    db_path = _write_desktop_fixture(tmp_path)
    adapter = OpenCodeAdapter()
    with patch("codetalker.adapters.opencode.default_opencode_db_path", return_value=None):
        sessions = adapter.discover_sessions(root_path=str(db_path))

    by_id = {s.session_id: s for s in sessions}
    assert len(by_id) == 2
    assert by_id["ses_empty123"].display_name == "Codetalker MCP access"
    assert by_id["ses_empty123"].working_directory
    assert "Agarth" in by_id["ses_empty123"].working_directory
    assert by_id["ses_named456"].display_name == "Build modpack goals"
