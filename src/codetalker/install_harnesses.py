"""Cross-platform CodeTalker MCP config installer.

Python port of scripts/install-harnesses.ps1 so macOS/Linux users get the
same one-command wiring. Stdlib only — runs before codetalker is installed.

What it does:
  - Merges a canonical codetalker MCP block into Cursor, Codex, Antigravity,
    and Claude Desktop config files when they exist.
  - Backs up every file it modifies to ``<path>.bak`` (overwriting only the
    backup, never accumulating backups).
  - Codex gets a TOML ``[mcp_servers.codetalker]`` block; the rest JSON.
  - Freebuff cannot be patched reliably from the CLI (client-managed consent
    sidecar); prints manual instructions instead, like the PS1.

Never touch harness configs without --dry-run first; the default is a dry
run that prints exactly what would change.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path

CODETALKER_TOML_BLOCK = (
    '[mcp_servers.codetalker]\n'
    'command = "{command}"\n'
    'args = [{args}]\n'
)

JSON_BLOCK_TOOL = {"command": "codetalker", "args": []}
JSON_BLOCK_UV = {"command": "{uv}", "args": ["run", "--project", "{root}", "codetalker"]}


def default_uv() -> str:
    """Locate uv: PATH first, then the usual install locations."""
    from shutil import which

    found = which("uv")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".local" / "bin" / "uv.exe",
        Path.home() / ".cargo" / "bin" / "uv",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return "uv"


def default_uvx() -> str:
    """Locate uvx (ships with uv): PATH first, then the usual locations."""
    from shutil import which

    found = which("uvx")
    if found:
        return found
    for c in (
        Path.home() / ".local" / "bin" / "uvx.exe",
        Path.home() / ".local" / "bin" / "uvx",
    ):
        if c.is_file():
            return str(c)
    return "uvx"


def default_command(mode: str, project_root: Path, uv: str | None = None) -> tuple[str, list[str]]:
    """The canonical launch command for MCP configs.

    Modes:
      uvx   — published package, ephemeral env: uvx --from codetalker-mcp codetalker
      tool  — bare shim from `uv tool install codetalker-mcp`: codetalker
      local — dev checkout: uv run --project <root> codetalker
    """
    if mode == "uvx":
        return default_uvx(), ["--from", "codetalker-mcp", "codetalker"]
    if mode == "tool":
        return "codetalker", []
    uv = uv or default_uv()
    return uv, ["run", "--project", str(project_root), "codetalker"]


def config_targets() -> dict[str, Path]:
    """Config paths per harness, resolved for the running OS."""
    home = Path.home()
    targets: dict[str, Path] = {}

    cursor = home / ".cursor" / "mcp.json"
    antigravity = home / ".gemini" / "antigravity" / "mcp_config.json"
    antigravity_alt = home / ".gemini" / "config" / "mcp_config.json"
    claude = home / ".claude" / "claude_desktop_config.json"
    codex = home / ".codex" / "config.toml"

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            claude = Path(appdata) / "Claude" / "claude_desktop_config.json"
    targets["Cursor"] = cursor
    targets["Antigravity"] = antigravity
    if antigravity_alt != antigravity:
        targets["Antigravity (config)"] = antigravity_alt
    targets["Claude"] = claude
    targets["Codex"] = codex
    return targets


def backup(path: Path) -> Path | None:
    if path.exists():
        bak = path.with_name(path.name + ".bak")
        shutil.copy2(path, bak)
        return bak
    return None


def merge_json_config(path: Path, command: str, args: list[str], dry_run: bool) -> str:
    """Merge the codetalker entry into an mcpServers-style JSON config."""
    if not path.exists():
        return f"[skip] {path} not found"

    try:
        # utf-8-sig: tolerate (and strip) a UTF-8 BOM, which Windows tooling
        # (e.g. PowerShell Set-Content -Encoding UTF8) commonly leaves behind
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        return f"[skip] {path}: unreadable ({e})"

    if not isinstance(config, dict):
        return f"[skip] {path}: unexpected top-level type {type(config).__name__}"

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        config["mcpServers"] = servers

    servers["codetalker"] = {"command": command, "args": args}

    if dry_run:
        return f"[dry-run] would update {path}"

    backup(path)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return f"[ok] updated {path}"


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\")


def update_codex_toml(path: Path, command: str, args: list[str], dry_run: bool) -> str:
    """Insert or replace the [mcp_servers.codetalker] block in Codex config."""
    if not path.exists():
        return f"[skip] {path} not found"

    block = CODETALKER_TOML_BLOCK.format(
        command=_toml_escape(command),
        args=", ".join(f'"{_toml_escape(a)}"' for a in args),
    )

    raw = path.read_text(encoding="utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    block = block.replace("\n", eol)

    pattern = re.compile(
        r"(?ms)^\[mcp_servers\.codetalker\].*?(?=^\[|\Z)"
    )
    if pattern.search(raw):
        # function replacement: re.sub would otherwise interpret backslash
        # escapes in the block (collapsing TOML's doubled "\\" back to "\")
        updated = pattern.sub(lambda _m: block.rstrip(eol) + eol * 2, raw)
        past, present = "replaced", "replace"
    else:
        updated = raw.rstrip(eol) + eol * 2 + block
        past, present = "appended", "append"

    if dry_run:
        return f"[dry-run] would {present} [mcp_servers.codetalker] in {path}"

    backup(path)
    path.write_text(updated, encoding="utf-8")
    return f"[ok] {past} [mcp_servers.codetalker] in {path}"


def install(
    project_root: Path | None = None,
    mode: str = "local",
    harnesses: list[str] | None = None,
    dry_run: bool = True,
    uv: str | None = None,
) -> list[str]:
    """Run the installer; returns per-target result lines."""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root).resolve()

    command, args = default_command(mode, project_root, uv)
    targets = config_targets()
    if harnesses:
        wanted = {h.lower() for h in harnesses}
        targets = {
            k: v for k, v in targets.items()
            if any(k.lower().startswith(w) for w in wanted)
        }

    results = [
        f"codetalker installer | root: {project_root} | "
        f"command: {command} {' '.join(args)} | mode: "
        f"{'DRY-RUN' if dry_run else 'WRITE'}"
    ]

    for name, path in targets.items():
        if name.startswith("Codex"):
            results.append(f"  {name:<22} {update_codex_toml(path, command, args, dry_run)}")
        else:
            results.append(f"  {name:<22} {merge_json_config(path, command, args, dry_run)}")

    freebuff_hint = (
        "  Freebuff (manual)      client-managed consent sidecar; remove and\n"
        "                         re-add codetalker in the Freebuff UI, then\n"
        "                         verify with codetalk_capabilities.\n"
        "  NOTE: the PowerShell variant (scripts/install-harnesses.ps1) adds\n"
        "        a uv-tool-install option on Windows; results are identical."
    )
    results.append(freebuff_hint)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codetalker-install",
        description="Merge codetalker MCP entries into local harness configs.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="codetalker repo path (default: parent of this package)",
    )
    parser.add_argument(
        "--mode",
        choices=("uvx", "tool", "local"),
        default=None,
        help="launch mode: uvx = published package (uvx --from codetalker-mcp), "
             "tool = bare shim after 'uv tool install codetalker-mcp', "
             "local = dev checkout (default: local, or tool if --uv-tool)",
    )
    parser.add_argument(
        "--uv-tool", action="store_true",
        help="legacy alias for --mode tool",
    )
    parser.add_argument(
        "--harness", action="append",
        help="limit to a harness (repeatable): Cursor, Codex, Antigravity, Claude",
    )
    parser.add_argument(
        "--write", action="store_true",
        help="actually modify config files (default is a dry run)",
    )
    ns = parser.parse_args(argv)

    mode = ns.mode
    if mode is None:
        mode = "tool" if ns.uv_tool else "local"

    for line in install(
        project_root=ns.project_root,
        mode=mode,
        harnesses=ns.harness,
        dry_run=not ns.write,
        uv=None,
    ):
        print(line)

    if not ns.write:
        print("\nThis was a DRY RUN. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
