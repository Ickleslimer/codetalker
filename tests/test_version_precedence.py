"""The server must report the SOURCE version, never stale dist metadata.

Editable installs don't refresh .dist-info on code edits, so dist metadata
can lag the running source. _package_version() therefore reads source
__version__ first and only falls back to dist metadata.
"""
from __future__ import annotations

import importlib.metadata

import codetalker
from codetalker import agent_guidance


def test_source_version_wins_over_dist_metadata(monkeypatch):
    """A stale/different dist version must not be reported."""
    monkeypatch.setattr(
        agent_guidance, "version", lambda _name: "9.9.9-stale-dist"
    )
    assert agent_guidance._package_version() == codetalker.__version__


def test_falls_back_to_dist_when_source_unreadable(monkeypatch):
    """No source attribute at all -> dist metadata is still consulted."""
    real_version = importlib.metadata.version

    def fake_version(name: str) -> str:
        if name == "codetalker-mcp":
            return "1.2.3"
        return real_version(name)

    monkeypatch.delattr(codetalker, "__version__", raising=False)
    monkeypatch.setattr(agent_guidance, "version", fake_version)
    assert agent_guidance._package_version() == "1.2.3"


def test_never_returns_placeholder_while_source_exists():
    """Sanity: on a normal install the function returns a real version."""
    assert agent_guidance._package_version() == codetalker.__version__
    assert agent_guidance._package_version() != "0.0.0"
