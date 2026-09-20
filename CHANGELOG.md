# Changelog

## 0.3.3 — 2026-09-20

- `codetalker-install --mode freebuff` now targets `~/.agents/mcp.json` — the
  launch registry the Freebuff desktop orchestrator actually reads (verified in
  the bundle) — creating it when missing; the previous config-twins target was
  never read by the app.
- New `scripts/codetalker_sync.py`: one command from working tree to a synced
  device (tests, version bump, branch-then-tag push, PyPI poll, shared uvx
  cache warm, pinned-env reset, launch-path proof, Freebuff registry refresh);
  `--no-publish` for local-only and `--dry-run` for previews.
- Fix: the server's self-reported version now prefers the source `__version__`
  over dist metadata, which editable installs never refresh (stale-version lie
  seen live: dist 0.3.1 vs source 0.3.2).

## 0.3.2 — 2026-09-19

- Release automation: the publish workflow now cuts the matching GitHub
  release automatically, with notes fetched live from the PyPI registry so
  release pages and PyPI descriptions stay paired. No code changes.

## 0.3.1 — 2026-09-19

- First PyPI release as `codetalker-mcp`.
- Cross-platform `codetalker-install` MCP config installer (dry-run default).
- Stranger-install smoke as 3-OS CI; publish workflow with wheel-smoke gate
  and PyPI trusted publishing.
- Fix: server self-reported version used a hardcoded dist-name lookup with a
  silent 0.0.0 fallback.

## 0.3.0 — 2026-09-19

- Trigger-gated context recovery, per-client MCP instructions, signed
  continue tokens (`codetalk_recover_token`).
