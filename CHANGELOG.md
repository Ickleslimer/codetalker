# Changelog

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
