# Changelog

## 0.3.6 — 2026-09-20

- Supersedes 0.3.5: that wheel was built by CI from the tagged commit, which
  predated the feature commit — so the published artifact lacked the issuance
  ledger entirely. The sync script now refuses to release from a dirty tree
  (the tag must equal the released code); this is the first run under that
  guard and ships the 0.3.5 feature set for real.

## 0.3.5 — 2026-09-20

- Change: continue-token verification is now **server-side** via an issuance
  ledger (`~/.codetalker/tokens.jsonl`, `CODETALKER_TOKEN_LEDGER` to override).
  Every anchor `codetalk_recover` emits is recorded at issuance;
  `codetalk_recover_token` verifies against the ledger without loading turns
  (transcript fallback preserved for pre-0.3.5 tokens; a signature-valid token
  matching no ledger and no transcript is refused).
- Change: agents are **no longer asked to append the `codetalker-v3-continue`
  line to their replies** — the visible per-turn echo is retired along with its
  ritual-drift failure mode; the token stays an internal, private artifact.
  Tool descriptions, server instructions, notes, and the decision tree updated;
  verification is strictly stronger (hand-crafted payloads are now refused).

## 0.3.4 — 2026-09-20

- Fix: the sync script's publish leg now refreshes the dev editable install's
  dist metadata after the version bump, verified by read-back — the
  metadata-behind-source drift seen in the 0.3.2/0.3.3 audits cannot recur.
- Fix: the sync script's uvx launch-path proof removes orphaned receipt-less
  tool environments (which pin resolution and refuse upgrades) and clears the
  dist's cached PyPI index pages before verifying — both failure modes found
  in the first live publish run.

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
