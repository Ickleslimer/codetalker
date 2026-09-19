#!/usr/bin/env python3
"""Stranger-install smoke: the onboarding path, executable.

Runs against a NON-EDITABLE codetalker install in the *current* interpreter's
environment — CI creates a fresh venv, installs the checked-out tree into it
(`uv pip install .`), and runs this script with that venv's python. This is
exactly what a stranger gets from `pip install git+https://...`, minus the
network hop (which would test the previous commit on push events).

Checks:
  1. the installed `codetalker` server exists on this venv's script path
  2. stdio handshake + full tool catalog (>=10 tools, core 8 present)
  3. v0.3 per-client instruction tailoring (freebuff mandate vs short fallback)
  4. capabilities answers, version matches the installed distribution
  5. with HOME/APPDATA/etc. redirected to an empty temp dir (a machine with
     zero agent sessions): capabilities and list answer gracefully, list
     returns count 0
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import sys
import sysconfig
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

DIST_NAMES = ("codetalker-mcp", "codetalker")  # PyPI name, then legacy

CORE_TOOLS = {
    "codetalk_capabilities",
    "codetalk_list",
    "codetalk_read",
    "codetalk_search",
    "codetalk_info",
    "codetalk_resolve_session",
    "codetalk_recover",
    "codetalk_recover_token",
}

EMPTY_HOME_VARS = (
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "XDG_DATA_HOME", "XDG_CONFIG_HOME",
)


def server_path() -> Path:
    scripts = Path(sysconfig.get_path("scripts"))
    name = "codetalker.exe" if os.name == "nt" else "codetalker"
    return scripts / name


def parse_json_output(text: str) -> dict | None:
    """Tool results are pretty-printed JSON; tolerate future reformatting."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


async def probe(client_name: str, env: dict | None = None) -> dict:
    params = StdioServerParameters(command=str(server_path()), args=[], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read,
            write,
            client_info=Implementation(name=client_name, version="0.0.0"),
        ) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            caps = await session.call_tool("codetalk_capabilities", {})
            listing = await session.call_tool("codetalk_list", {"limit": 5})
            return {
                "instructions": init.instructions or "",
                "tools": sorted(t.name for t in tools.tools),
                "caps": caps,
                "listing": listing,
            }


async def main() -> int:
    checks: dict[str, bool] = {}

    # 1. server exists on the venv path
    sp = server_path()
    checks[f"server installed at {sp}"] = sp.is_file()

    # 2-4. handshake with real environment
    a_fb = await probe("freebuff")
    a_other = await probe("generic-editor")
    checks[">=10 tools registered"] = len(a_fb["tools"]) >= 10
    checks["core 8 tools present"] = CORE_TOOLS.issubset(set(a_fb["tools"]))

    fb_instr = a_fb["instructions"]
    other_instr = a_other["instructions"]
    checks["freebuff client gets marker-gated mandate"] = (
        "<failed_turn>" in fb_instr and "codetalker-v3-continue" in fb_instr
    )
    checks["generic client gets short fallback"] = (
        len(other_instr) < len(fb_instr) and "<failed_turn>" not in other_instr
    )
    checks["instructions differ per client"] = fb_instr != other_instr

    caps = a_fb["caps"]
    caps_json = parse_json_output(caps.content[0].text) if caps.content else None
    checks["capabilities answers without error"] = not caps.is_error
    if caps_json:
        dist_version = None
        for dist_name in DIST_NAMES:
            try:
                dist_version = importlib.metadata.version(dist_name)
                break
            except importlib.metadata.PackageNotFoundError:
                continue
        checks["server version matches installed dist"] = (
            dist_version is not None
            and caps_json.get("server", {}).get("version") == dist_version
        )
        checks["capabilities reports project_root"] = bool(
            caps_json.get("server", {}).get("project_root")
        )
    checks["list answers without error (real home)"] = not a_fb["listing"].is_error

    # 5. empty home: a machine with zero agent sessions must degrade gracefully
    empty = Path(tempfile.mkdtemp(prefix="stranger-home-"))
    env = dict(os.environ)
    for var in EMPTY_HOME_VARS:
        env[var] = str(empty)
    b = await probe("generic-editor", env=env)
    checks["empty-home capabilities graceful"] = not b["caps"].is_error
    checks["empty-home list graceful"] = not b["listing"].is_error
    listing_json = (
        parse_json_output(b["listing"].content[0].text) if b["listing"].content else None
    )
    if listing_json is not None:
        checks["empty-home list returns count 0"] = listing_json.get("count") == 0

    passed = all(checks.values())
    print(f"stranger smoke: {sum(checks.values())}/{len(checks)} checks pass")
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
