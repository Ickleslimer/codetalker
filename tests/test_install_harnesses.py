"""Tests for the cross-platform MCP config installer (install_harnesses).

All paths are redirected into tmp_path fakes — the real user configs are
never touched. uv resolution is injected explicitly to stay hermetic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codetalker import install_harnesses as ih


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() into tmp_path/home and seal APPDATA (Windows
    Claude path source) inside tmp_path, so write-mode tests can never
    touch real user configs on any machine."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(ih.Path, "home", classmethod(lambda cls: home))
    env = dict(ih.os.environ)
    env["APPDATA"] = str(tmp_path / "appdata")
    monkeypatch.setattr(ih.os, "environ", env)
    return home


@pytest.fixture
def known_uv(tmp_path, monkeypatch):
    """Pin uv resolution to a stable fake path so results are deterministic."""
    uv = tmp_path / "uv-bin"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setattr(ih, "default_uv", lambda: str(uv))
    return uv


# ---------------------------------------------------------------------------
# JSON merge behavior
# ---------------------------------------------------------------------------

def test_json_merge_creates_mcp_servers(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"other": True})

    root = fake_home / "codetalker"
    ih.install(project_root=root, dry_run=False, uv=str(known_uv))

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["other"] is True
    entry = data["mcpServers"]["codetalker"]
    assert entry["command"] == str(known_uv)
    assert entry["args"] == ["run", "--project", str(root), "codetalker"]


def test_json_merge_preserves_other_servers(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {"other-tool": {"command": "x", "args": ["1"]}}})

    ih.install(project_root=fake_home / "repo", dry_run=False, uv=str(known_uv))

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other-tool"] == {"command": "x", "args": ["1"]}
    assert "codetalker" in data["mcpServers"]


def test_json_merge_is_idempotent(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {}})
    for _ in range(3):
        ih.install(project_root=fake_home / "repo", dry_run=False, uv=str(known_uv))
    data = json.loads(cfg.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert len(servers) == 1
    assert servers["codetalker"]["command"] == str(known_uv)


def test_json_backup_created_once_and_overwritten(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {}})
    original = cfg.read_text(encoding="utf-8")

    ih.install(project_root=fake_home / "repo", dry_run=False, uv=str(known_uv))
    bak = cfg.with_name(cfg.name + ".bak")
    assert bak.exists()
    assert json.loads(bak.read_text(encoding="utf-8")) == {"mcpServers": {}}

    # second run: backup holds the PRE-RUN state of run 2 (with codetalker),
    # never accumulated (always exactly one .bak) and still valid json
    ih.install(project_root=fake_home / "repo", dry_run=False, uv=str(known_uv))
    assert json.loads(bak.read_text(encoding="utf-8")) == {
        "mcpServers": {"codetalker": {
            "command": str(known_uv),
            "args": ["run", "--project", str(fake_home / "repo"), "codetalker"],
        }}
    }
    assert cfg.read_text(encoding="utf-8") != original


def test_json_skip_when_missing(fake_home, known_uv):
    results = ih.install(
        project_root=fake_home / "repo", dry_run=False,
        harnesses=["Cursor"], uv=str(known_uv))
    assert any("[skip]" in r and "not found" in r for r in results)


def test_json_skip_on_corrupt(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{not json", encoding="utf-8")
    results = ih.install(
        project_root=fake_home / "repo", dry_run=False,
        harnesses=["Cursor"], uv=str(known_uv))
    assert any("[skip]" in r and "unreadable" in r for r in results)
    # corrupt file must be untouched
    assert cfg.read_text(encoding="utf-8") == "{not json"


def test_json_tolerates_utf8_bom(fake_home, known_uv):
    """PowerShell's Set-Content -Encoding UTF8 leaves a BOM; the installer
    must still merge cleanly and strip it (found on a real machine)."""
    cfg = fake_home / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {}}).encode())
    ih.install(project_root=fake_home / "repo", dry_run=False,
               harnesses=["Cursor"], uv=str(known_uv))
    data = json.loads(cfg.read_text(encoding="utf-8-sig"))
    assert "codetalker" in data["mcpServers"]
    assert not cfg.read_bytes().startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# Codex TOML behavior
# ---------------------------------------------------------------------------

def test_codex_toml_appends_block(fake_home, known_uv):
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text('[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")

    ih.install(project_root=fake_home / "repo", dry_run=False,
               harnesses=["Codex"], uv=str(known_uv))

    text = toml.read_text(encoding="utf-8")
    assert "[mcp_servers.other]" in text
    assert "[mcp_servers.codetalker]" in text
    # Windows paths get TOML-escaped (backslashes doubled) in the written file
    uv_escaped = str(known_uv).replace("\\", "\\\\")
    assert f'command = "{uv_escaped}"' in text


def test_codex_toml_replaces_existing_block(fake_home, known_uv):
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        '[mcp_servers.codetalker]\ncommand = "OLD"\nargs = []\n\n'
        '[mcp_servers.after]\ncommand = "keep"\n',
        encoding="utf-8",
    )

    ih.install(project_root=fake_home / "repo", dry_run=False,
               harnesses=["Codex"], uv=str(known_uv))

    text = toml.read_text(encoding="utf-8")
    assert 'command = "OLD"' not in text
    # Windows paths get TOML-escaped (backslashes doubled) in the written file
    uv_escaped = str(known_uv).replace("\\", "\\\\")
    assert f'command = "{uv_escaped}"' in text
    # the following block survives
    assert "[mcp_servers.after]" in text
    assert 'command = "keep"' in text
    assert text.count("[mcp_servers.codetalker]") == 1


def test_codex_toml_idempotent(fake_home, known_uv):
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text("", encoding="utf-8")
    for _ in range(3):
        ih.install(project_root=fake_home / "repo", dry_run=False,
                   harnesses=["Codex"], uv=str(known_uv))
    text = toml.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.codetalker]") == 1
    assert "OLD" not in text


def test_codex_toml_replacement_preserves_toml_escapes(fake_home):
    """re.sub with a string replacement collapses TOML's doubled backslashes
    (would corrupt every Windows Codex config). Function replacement must
    keep them. Regression test for a bug found before first release."""
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text('[mcp_servers.codetalker]\ncommand = "OLD"\n', encoding="utf-8")

    win_uv = r"C:\Users\mrdyl\uv.EXE"  # single backslashes in the real path
    ih.install(project_root=r"D:\repo", dry_run=False,
               harnesses=["Codex"], uv=win_uv)

    text = toml.read_text(encoding="utf-8")
    assert r'command = "C:\\Users\\mrdyl\\uv.EXE"' in text  # doubled in TOML
    assert 'command = "OLD"' not in text
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_bytes(b"[mcp_servers.other]\r\ncommand = \"x\"\r\n")

    ih.install(project_root=fake_home / "repo", dry_run=False,
               harnesses=["Codex"], uv=str(known_uv))

    data = toml.read_bytes()
    assert b"[mcp_servers.codetalker]\r\n" in data


# ---------------------------------------------------------------------------
# dry-run + uv-tool mode + filtering
# ---------------------------------------------------------------------------

def test_dry_run_is_the_default_and_touches_nothing(fake_home, known_uv):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {}})
    before = cfg.read_text(encoding="utf-8")

    results = ih.install(project_root=fake_home / "repo", dry_run=True,
                         uv=str(known_uv))

    assert cfg.read_text(encoding="utf-8") == before
    assert not cfg.with_name(cfg.name + ".bak").exists()
    assert any("[dry-run]" in r for r in results)


def test_uv_tool_mode_uses_bare_command(fake_home, known_uv):
    write_json(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    results = ih.install(project_root=fake_home / "repo", dry_run=False,
                         mode="tool", harnesses=["Cursor"])
    data = json.loads(
        (fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["codetalker"] == {"command": "codetalker", "args": []}
    assert any("[ok]" in r for r in results)


def test_uvx_mode_uses_published_package(fake_home, known_uv, monkeypatch):
    write_json(fake_home / ".cursor" / "mcp.json", {"mcpServers": {}})
    uvx = known_uv.parent / "uvx-bin"
    uvx.write_text("", encoding="utf-8")
    monkeypatch.setattr(ih, "default_uvx", lambda: str(uvx))
    ih.install(project_root=fake_home / "repo", dry_run=False,
               mode="uvx", harnesses=["Cursor"])
    data = json.loads(
        (fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["codetalker"] == {
        "command": str(uvx), "args": ["--from", "codetalker-mcp", "codetalker"]
    }


def test_uvx_toml_block_in_codex(fake_home, known_uv, monkeypatch):
    toml = fake_home / ".codex" / "config.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text("", encoding="utf-8")
    monkeypatch.setattr(ih, "default_uvx", lambda: "uvx")
    ih.install(project_root=fake_home / "repo", dry_run=False,
               mode="uvx", harnesses=["Codex"])
    text = toml.read_text(encoding="utf-8")
    assert 'command = "uvx"' in text
    assert '["--from", "codetalker-mcp", "codetalker"]' in text


def test_harness_filter_limits_targets(fake_home, known_uv):
    results = ih.install(project_root=fake_home / "repo", dry_run=False,
                         harnesses=["Cursor"], uv=str(known_uv))
    joined = "\n".join(results)
    assert "Cursor" in joined
    assert "Codex" not in joined
    assert not (fake_home / ".codex" / "config.toml").exists()


def test_antigravity_alt_config_picked_up(fake_home, known_uv):
    alt = fake_home / ".gemini" / "config" / "mcp_config.json"
    write_json(alt, {"mcpServers": {}})
    ih.install(project_root=fake_home / "repo", dry_run=False,
               harnesses=["Antigravity"], uv=str(known_uv))
    data = json.loads(alt.read_text(encoding="utf-8"))
    assert "codetalker" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Windows-specific Claude path logic (pure logic, no real paths touched)
# ---------------------------------------------------------------------------

def test_claude_path_uses_appdata_on_windows(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"
    monkeypatch.setattr(ih.sys, "platform", "win32")
    monkeypatch.setattr(ih.os, "environ", {"APPDATA": str(appdata)})
    monkeypatch.setattr(ih.Path, "home", classmethod(lambda cls: tmp_path / "h"))
    targets = ih.config_targets()
    assert targets["Claude"] == appdata / "Claude" / "claude_desktop_config.json"


def test_claude_path_dotclaude_on_posix(monkeypatch, tmp_path):
    monkeypatch.setattr(ih.sys, "platform", "linux")
    monkeypatch.setattr(ih.Path, "home", classmethod(lambda cls: tmp_path))
    targets = ih.config_targets()
    assert targets["Claude"] == tmp_path / ".claude" / "claude_desktop_config.json"


# ---------------------------------------------------------------------------
# main() CLI wiring
# ---------------------------------------------------------------------------

def test_main_defaults_to_dry_run(fake_home, known_uv, capsys):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {}})
    before = cfg.read_text(encoding="utf-8")

    rc = ih.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert cfg.read_text(encoding="utf-8") == before


def test_main_write_applies(fake_home, known_uv, capsys):
    cfg = fake_home / ".cursor" / "mcp.json"
    write_json(cfg, {"mcpServers": {}})
    rc = ih.main(["--write", "--harness", "Cursor"])
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "codetalker" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Freebuff config twins (--mode freebuff)
# ---------------------------------------------------------------------------

@pytest.fixture
def known_uvx(tmp_path, monkeypatch):
    """Pin uvx resolution so the freebuff mode's command is deterministic."""
    uvx = tmp_path / "uvx-bin"
    uvx.write_text("", encoding="utf-8")
    monkeypatch.setattr(ih, "default_uvx", lambda: str(uvx))
    return uvx


def _freebuff_paths(home: Path) -> tuple[Path, Path]:
    base = home / ".config" / "freebuff-desktop"
    return base / "mcp.json", base / "mcp_config.json"


def test_freebuff_targets_in_config_targets(fake_home):
    main, twin = _freebuff_paths(fake_home)
    targets = ih.config_targets()
    assert targets["Freebuff (config)"] == main
    assert targets["Freebuff (config twin)"] == twin


def test_freebuff_mode_creates_missing_twins(fake_home, known_uvx):
    main, twin = _freebuff_paths(fake_home)
    assert not main.exists() and not twin.exists()

    ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    data = json.loads(main.read_text(encoding="utf-8"))
    assert data == json.loads(twin.read_text(encoding="utf-8"))
    entry = data["mcpServers"]["codetalker"]
    assert entry["command"] == str(known_uvx)
    assert entry["args"] == ["--from", "codetalker-mcp", "codetalker"]
    assert ih.FREEBUFF_COMMENT_KEY in data


def test_freebuff_mode_dry_run_creates_nothing(fake_home):
    main, twin = _freebuff_paths(fake_home)
    results = ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=True)
    assert not main.exists() and not twin.exists()
    assert any("[dry-run] would create" in r for r in results)


def test_freebuff_mode_merges_existing_preserving_others(fake_home, known_uvx):
    main, twin = _freebuff_paths(fake_home)
    for p in (main, twin):
        write_json(p, {"mcpServers": {"desktop-control": {"command": "dc.exe", "args": []}}})

    ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    for p in (main, twin):
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["mcpServers"]["desktop-control"] == {"command": "dc.exe", "args": []}
        assert data["mcpServers"]["codetalker"]["command"] == str(known_uvx)


def test_freebuff_mode_replaces_dev_checkout_form(fake_home, known_uvx):
    main, twin = _freebuff_paths(fake_home)
    old = {"mcpServers": {"codetalker": {
        "command": "D:\\codetalker\\.venv\\Scripts\\codetalker.exe", "args": []}}}
    write_json(main, old)
    write_json(twin, old)

    ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    for p in (main, twin):
        entry = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["codetalker"]
        assert entry["command"] == str(known_uvx)
        assert "--from" in entry["args"]


def test_freebuff_mode_preserves_crlf(fake_home):
    main, twin = _freebuff_paths(fake_home)
    main.parent.mkdir(parents=True, exist_ok=True)
    crlf = '{\r\n  "mcpServers": {}\r\n}\r\n'
    main.write_text(crlf, encoding="utf-8")
    twin.write_text(crlf, encoding="utf-8")

    ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    raw = main.read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_freebuff_mode_skips_corrupt(fake_home):
    main, twin = _freebuff_paths(fake_home)
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text("{not json", encoding="utf-8")
    twin.write_text("{not json", encoding="utf-8")

    results = ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    assert any("[skip]" in r and "unreadable" in r for r in results)
    assert main.read_text(encoding="utf-8") == "{not json"


def test_freebuff_mode_backs_up_existing(fake_home):
    main, twin = _freebuff_paths(fake_home)
    write_json(main, {"mcpServers": {}})
    write_json(twin, {"mcpServers": {}})

    ih.install(project_root=fake_home / "repo", mode="freebuff", dry_run=False)

    for p in (main, twin):
        bak = p.with_name(p.name + ".bak")
        assert bak.exists()
        assert json.loads(bak.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_freebuff_harness_filter(fake_home, known_uvx):
    results = ih.install(
        project_root=fake_home / "repo", mode="freebuff",
        harnesses=["Freebuff"], dry_run=True)
    assert any("Freebuff (config)" in r for r in results)
    assert not any("Cursor" in r for r in results)


def test_main_freebuff_mode_write(fake_home, known_uvx, capsys):
    main, _ = _freebuff_paths(fake_home)
    rc = ih.main(["--mode", "freebuff", "--write", "--harness", "Freebuff"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" not in out
    data = json.loads(main.read_text(encoding="utf-8"))
    assert data["mcpServers"]["codetalker"]["command"] == str(known_uvx)
