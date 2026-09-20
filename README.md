# CodeTalker

[![PyPI version](https://img.shields.io/pypi/v/codetalker-mcp.svg)](https://pypi.org/project/codetalker-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/codetalker-mcp.svg)](https://pypi.org/project/codetalker-mcp/)
[![stranger-smoke CI](https://github.com/Ickleslimer/codetalker/actions/workflows/stranger-smoke.yml/badge.svg)](https://github.com/Ickleslimer/codetalker/actions/workflows/stranger-smoke.yml)

> Cross-harness agent conversation transcript normalizer and MCP server.

CodeTalker is an agent-callable tool and MCP server that normalizes conversation transcripts from different AI coding harnesses into a unified schema. This allows any agent to pick up context, search past decisions, or read thread history without requiring manual handoff documents.

---

> [!IMPORTANT]
> **Privacy: read-only, local-only, no telemetry.**
> CodeTalker **reads** your agent harnesses' local conversation history and nothing else.
> - **Read-only by construction.** Every harness database it touches (Cursor, Freebuff, OpenCode, Windsurf/Devin) is opened in SQLite read-only mode (`?mode=ro`) — the driver itself refuses writes, so a bug in CodeTalker cannot modify your sessions. It writes no config files, keeps no cache, stores no state of its own.
> - **What it touches.** Only the harnesses' own storage directories listed in the [support table below](#supported-harnesses--verification-status) (e.g. `~/.codex/sessions`, `~/.config/freebuff-desktop`, `%APPDATA%/Cursor`), plus local OpenCode desktop logs to discover that app's local server port. Nothing on your machine is modified.
> - **Nothing is sent anywhere.** The server speaks stdio only — it talks exclusively to the agent harness that launched it, on your machine. There is no telemetry, no analytics, no update checks. The one network-shaped exception is disclosed: the OpenCode sidecar adapter may issue a **local** HTTP GET to your own running OpenCode desktop app (port discovered from that app's local logs) to read session messages. No transcript data ever leaves your machine via CodeTalker — it leaves only if the agent harness you use sends tool results to its own model backend, which is outside CodeTalker's control.

---

## Capabilities & Schema

- **Normalized Intermediate Format**: Standardized `TextBlock`, `ThinkingBlock`, `ToolCallBlock`, `ToolResultBlock`, `CodeDiffBlock`, `AttachmentBlock`, `ApprovalBlock`, `SystemEventBlock`.
- **DAG / Branch Aware**: Multi-branch threads (e.g. in ChatGPT/Codex or Claude Code) are exposed as distinct threads sharing a conversation ID.
- **Fast Metadata Discovery**: Fast header peeking and recency sorting for collections with 500+ session files.

---

## Supported Harnesses & Verification Status

| Harness | Aliases | Storage Locations | Test Status | Notes |
|---|---|---|---|---|
| **OpenAI Codex CLI** | `codex`, `chatgpt` | `~/.codex/sessions/**/rollout-*.jsonl`, `session_index.jsonl` | **Live Verified** | Tested across 480+ local CLI sessions with trailing timestamps and DAG resolution. |
| **OpenAI ChatGPT Desktop** | `chatgpt` | `%LOCALAPPDATA%/Packages/OpenAI.ChatGPT-Desktop_*/.../IndexedDB` | **Live Verified** | Tested via `ccl-chromium-reader` LevelDB parser. *(See fragility disclaimer below)*. |
| **ChatGPT Export DAG** | `chatgpt` | `conversations.json` (Export Archive) | **Live Verified** | Linearizes branching conversation DAG trees into distinct threads. |
| **Devin (formerly Windsurf)** | `devin`, `windsurf` | `~/.codeium/chat_state/*.pb`, `state.vscdb` | **Live Verified** | Pure-Python wire-level Protobuf stream parser and workspace SQLite reader. |
| **Freebuff** | `freebuff`, `codebuff` | `~/.config/freebuff-desktop/projects/*/desktop-v2.db` | **Live Verified** | Full multi-turn conversation logs, reasoning traces, image attachments, and tool calls. |
| **OpenCode Desktop** | `opencode`, `open_code` | `%APPDATA%/ai.opencode.desktop/drafts.sqlite` | **Live Verified** | Decodes workspace paths, models, prompt histories, and active session drafts. *(See notes below)*. |
| **Google Antigravity** | `antigravity`, `agy` | `~/.gemini/antigravity/brain/*/transcript.jsonl` | **Live Verified** | Real-time transcript logs, XML cleanup, subagent trees, thinking blocks, and checkpoints. |
| **Cursor IDE** | `cursor` | `%APPDATA%/Cursor/User/globalStorage/state.vscdb` | **Live Verified** | Scans `composerHeaders` across 50+ workspaces, bubbles, diffs, and reasoning traces. |
| **Claude Code CLI** | `claude`, `claudecode` | `~/.claude/projects/*/sessions/*.jsonl` | **Fixture Tested (YMMV)** | Implemented against Anthropic Messages API specs; not verified against an active local installation. |
| **Aider Pair Programmer** | `aider` | `.aider.chat.history.md`, `~/.aider.chat.history.md` | **Fixture Tested (YMMV)** | Implemented for markdown chat logs and `<<<< SEARCH ... === ... >>>>` diffs; not installed locally. |
| **GitHub Copilot Chat** | `copilot`, `github_copilot` | `%APPDATA%/Code/User/workspaceStorage/*/chatSessions/*.jsonl` | **Fixture Tested (YMMV)** | Implemented for VSCode chat session JSONL logs; not verified against an active local installation. |

---

## Stability, Fragility & Compatibility Disclaimers

> [!WARNING]
> **ChatGPT Desktop App (LevelDB Cache) Fragility**
> The ChatGPT Desktop application uses Chromium IndexedDB / LevelDB to cache conversation state locally. This storage engine is unversioned, undocumented, and frequently modified by OpenAI between app updates.
> - **Recommendation**: For reliable long-term retrieval, prefer **Codex CLI rollouts** (`~/.codex/sessions`) or the official data export (`conversations.json`).

> [!NOTE]
> **OpenCode Desktop Cloud Streaming vs Local Drafts**
> OpenCode Desktop persists active drafts, models (`grok`, `gpt-5.6`, `x-preview`), and user prompt history in `%APPDATA%/ai.opencode.desktop/drafts.sqlite`. Because multi-turn assistant completions are rendered via live server-side WebSockets, local desktop records represent client-side prompts and active workspace drafts. Full multi-turn assistant outputs and tool executions are available if using **OpenCode CLI** JSONL logs (`~/.opencode/sessions/*.jsonl`).

> [!IMPORTANT]
> **Cursor SQLite Schema Evolution**
> Cursor's internal storage schema in `state.vscdb` (`composerHeaders`, `cursorDiskKV`, `composerData`, `bubbleId`) evolves across Cursor releases. CodeTalker connects in read-only mode (`?mode=ro`) with schema fallbacks, but major upstream Cursor redesigns may require updating field mappings.

> [!TIP]
> **Fixture-Tested Adapters (YMMV)**
> The adapters for **Claude Code CLI**, **Aider**, and **GitHub Copilot Chat** have complete normalization logic verified by unit test fixtures, but have not been live-tested against active local installations on this machine. If you use these tools and encounter non-standard directory structures or version variations, use the `root_path` parameter to point CodeTalker directly to your transcript folder.

---

## MCP Tools

| Tool | Parameters | Description |
|---|---|---|
| `codetalk_capabilities` | _(none)_ | List harnesses, aliases, ID guidance, context-recovery playbook, and recommended read defaults. Call once per agent session. |
| `codetalk_list` | `harness`, `conversation_id`, `working_directory`, `since`, `limit`, `root_path`, `include_capabilities`, `include_harness_status` | List sessions (slim by default). Filter by `working_directory` for project-scoped recovery. |
| `codetalk_resolve_session` | `working_directory`, `harness`, `display_name`, `root_path`, `limit` | Resolve the most recent session for a project path when `session_id` is unknown (common Freebuff context-loss recovery). Optional `display_name` narrows by thread title. |
| `codetalk_read` | `session_id`, `harness`, `working_directory`, `since`, `until`, `since_last_user_input`, `conversation_only`, `exclude_actor_roles`, `include_thinking`, `include_raw_data`, `max_step_chars`, `offset`, `from_end`, `limit`, `root_path` | Read normalized steps. Provide `session_id` **or** `working_directory`. Defaults: tail slice (`from_end=true`), conversation-only (`conversation_only=true`), no raw payloads (`include_raw_data=false`). |
| `codetalk_branches` | `conversation_id`, `harness`, `root_path` | DAG branch tree, fork points, and subagent hierarchy (`branch_id` usually equals `session_id`). |
| `codetalk_diff_branches` | `conversation_id`, `branch_a`, `branch_b`, `harness`, `summary_only`, `include_raw_data`, `limit_per_branch`, `from_end`, `root_path` | Compare branches. Defaults to `summary_only=true` (counts/metadata only). |
| `codetalk_filter` | `session_id`, `harness`, `working_directory`, `keywords`, `step_types`, `actor_roles`, `conversation_only`, `exclude_actor_roles`, `since_last_user_input`, `include_thinking`, `include_raw_data`, `max_step_chars`, `offset`, `from_end`, `limit`, `root_path` | Filter steps by keywords, types, or roles. Accepts `session_id` or `working_directory`. |
| `codetalk_search` | `query`, `harness`, `working_directory`, `since`, `limit`, `max_sessions_to_search`, `search_scope`, `root_path` | Search titles and transcript content. Pass `working_directory` or `harness` when scoped to one project. Title hits use `match_type=title`. |
| `codetalk_info` | `session_id`, `harness`, `working_directory`, `root_path` | Fast metadata without step bodies (refreshes step counts when possible). Accepts `session_id` or `working_directory`. |

### Agent quickstart

1. `codetalk_capabilities` — learn harness names, aliases, tool catalog, and unsupported hallucinated names (`read_transcript`, etc.).
2. **Decision tree:**
   - Lost context + know project path → `codetalk_resolve_session` → `codetalk_read(since_last_user_input=true)`
   - Know `session_id` → `codetalk_read`
   - Grep / find by title → `codetalk_search(query=..., working_directory=... or harness=...)`
   - Branch history → `codetalk_branches` / `codetalk_diff_branches`
3. `codetalk_list` — browse metadata; filter with `working_directory` and/or `harness` on busy machines.
4. `codetalk_read` with defaults — tail slice without system injections or `raw_data`.

Note: Codex CLI rollouts appear under harness `chatgpt`; use `session_id` for reads and `conversation_id` for branch tools.

### Per-harness MCP onboarding

| Harness | Setup notes |
|---|---|
| **Cursor / Antigravity / Claude Desktop** | Add MCP block with `uv run --project /path/to/codetalker codetalker`. Restart after config changes. |
| **Freebuff** | Config in `~/.config/freebuff-desktop`. Approve the MCP consent sidecar when prompted, then restart. Verify with `codetalk_capabilities`. |
| **Codex desktop** | MCP config differs from CLI; mirror a working Cursor/Antigravity definition if supported. Desktop may not expose MCP. |
| **OpenCode** | Desktop drafts are prompt-only; use CLI JSONL or `codetalk_search(query='<thread title>')` for cross-harness title lookup. |

`codetalk_capabilities` and `codetalk_info` return `server.project_root` — update MCP config if it points at a stale scratch copy.

### Context recovery (Freebuff-first)

Some harnesses lose **in-flight prompt context** while the **full transcript remains on disk**. Freebuff is the most common case: the agent may reply with *"I can't see the session context…"* even though `desktop-v2.db` still has every turn.

**Symptom → fix**

1. User says *continue* but the Freebuff agent is blind.
2. Call `codetalk_resolve_session(working_directory="<project path>", harness="freebuff")` to get the latest `session_id` for that repo.
3. Call `codetalk_read(working_directory="<project path>", harness="freebuff", since_last_user_input=true)` — or pass the resolved `session_id` — to recover what the user last asked and what the agent already did.
4. Optionally `codetalk_search(query="can't see the session context", harness="freebuff")` to find other threads that hit the same failure.

`working_directory` accepts plain paths (`C:/path/to/myproject`) or `file://` URIs. Matching is normalized and case-insensitive on Windows. You do **not** need `session_id` when you know the project path — `codetalk_read` and `codetalk_info` accept `working_directory` directly.

Cross-harness recovery works too: open any harness with CodeTalker MCP configured (e.g. Cursor), point it at the Freebuff `working_directory`, and read the persisted transcript from there.

`codetalk_capabilities` returns the full recovery playbook in `context_recovery`.

#### v0.3: trigger-gated recovery + continue tokens

Since v0.3 the recovery mandate is **trigger-gated and per-client**:

- **Per-client instructions.** The handshake tailors `instructions` to the connecting client (via `clientInfo.name`): harnesses with known mid-thread context loss (Freebuff) receive the full marker-gated mandate; every other harness receives a short fallback. Healthy turns on any harness do **zero** recovery work.
- **Mechanical wipe markers.** Restart/failed-turn notices (`<since_your_last_turn>`, `<failed_turn>`, session-ended system notices) are detected in the transcript tail — no model judgment required for the loud class of wipes.
- **Continue tokens.** `codetalk_recover` now returns a `continue_token` line (`codetalker-v3-continue {…}`): an integrity-signed anchor (session, working directory, last user turn, transcript length). Agents end substantive turns with it; a later wiped turn passes it back as `claimed_token`, and the server verifies the agent's memory against the transcript on disk — anchors that were silently dropped or edited fail verification. `codetalk_recover_token` is the verification-only form. Silent mid-session wipes leave no transcript artifact, so their detection stays with the antecedent check — the token makes the recovery **verifiable** instead of guessed.
- **Freebuff consent sidecar.** `codetalk_recover_token` is new, so Freebuff requires a one-time tool re-approval in the Freebuff UI (remove and re-add the codetalker server) before the tool is callable there.

---

## Installation & Setup

### Install from PyPI

Published as **`codetalker-mcp`** (the name `codetalker` on PyPI belongs to an
unrelated 2014 package):

```bash
pip install codetalker-mcp
# or
uv tool install codetalker-mcp
```

MCP config entries then need no repo path:

```json
{
  "mcpServers": {
    "codetalker": {
      "command": "uvx",
      "args": ["--from", "codetalker-mcp", "codetalker"]
    }
  }
}
```

> [!NOTE]
> **ChatGPT Desktop adapter dependency.** The ChatGPT Desktop (IndexedDB/LevelDB)
> adapter relies on `ccl-chromium-reader`, which is **only available from GitHub**
> (it has no PyPI package, so it cannot be a pip dependency). Every other adapter
> works out of the box. To enable ChatGPT Desktop support:
>
> ```bash
> pip install "git+https://github.com/cclgroupltd/ccl_chromium_reader.git"
> ```
>
> Without it, that one adapter reports `registered: false` / fails gracefully;
> Codex CLI rollouts (harness `codex`/`chatgpt`) are unaffected.

### Running locally (development)
```bash
uv sync
uv run pytest -v
uv run codetalker --log-level INFO
```

### Propagating MCP config after a move or clone

When the repo moves (e.g. to `D:/codetalker`), every harness MCP entry must point at the new path. Run the installer from the repo root:

```powershell
.\scripts\install-harnesses.ps1 -ProjectRoot D:\codetalker
```

What it updates (when those config files exist on your machine):

| Harness | Config file |
|---|---|
| **Cursor** | `%USERPROFILE%\.cursor\mcp.json` |
| **Codex** | `%USERPROFILE%\.codex\config.toml` (`[mcp_servers.codetalker]`) |
| **Antigravity** | `%USERPROFILE%\.gemini\antigravity\mcp_config.json` |
| **Claude Desktop** | `%APPDATA%\Claude\claude_desktop_config.json` |

Each file is backed up to `*.bak` before overwrite. **Freebuff** is not patched automatically — remove and re-add codetalker in the Freebuff client UI so a fresh MCP approval is minted (see script output for suggested command/args).

Optional path-independent mode (installs a global `codetalker` shim via uv):

```powershell
.\scripts\install-harnesses.ps1 -UseUvTool
```

Limit to specific harnesses: `-Harness Cursor,Codex`. Preview changes: `-WhatIf`.

After running, restart each harness and call `codetalk_capabilities` — confirm `server.project_root` matches your install.

### Cross-platform installer (macOS / Linux / any OS)

The same wiring logic ships as a stdlib-only Python entry point — usable immediately
after `pip install git+https://github.com/Ickleslimer/codetalker.git`, no PowerShell
required:

```bash
# preview what would change (default; modifies nothing)
codetalker-install --project-root /path/to/codetalker

# apply
codetalker-install --project-root /path/to/codetalker --write

# uv tool users (after: uv tool install /path/to/codetalker)
codetalker-install --uv-tool --write
```

Targets are the same as the PowerShell script (Cursor, Antigravity, Claude Desktop,
Codex TOML) plus **Freebuff desktop's launch registry** (`~/.agents/mcp.json`,
the path verified inside the Freebuff orchestrator bundle), which is created
when missing and merged in place when present — so a fresh machine needs zero
hand-editing. Existing `codetalker` entries are replaced in place, other MCP
servers are preserved, every modified file gets a one-shot `.bak` backup, and
CRLF line endings survive on Windows-written configs. The Claude Desktop config
resolves to `%APPDATA%\Claude\claude_desktop_config.json` on Windows and
`~/.claude/claude_desktop_config.json` elsewhere.

One step always stays manual: after writing Freebuff's registry (or on first
run), **restart Freebuff and approve the codetalker manifest in the UI** — the
consent sidecar (`~/.freebuff/mcp.json`) is client-managed and its
mutation endpoints are launch-token-gated by design. On Windows, either
installer works; the PowerShell variant additionally offers `uv tool install`
integration.

### Adding to MCP Configuration (manual)

In your agent harness MCP config (e.g., Antigravity, Claude Desktop, Cursor):

```json
{
  "mcpServers": {
    "codetalker": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/path/to/codetalker",
        "codetalker"
      ]
    }
  }
}
```

### Development: the stranger-install smoke

CI (`.github/workflows/stranger-smoke.yml`) keeps the onboarding path honest on
every push/PR: on ubuntu, macos, and windows runners it creates a **fresh venv**,
installs the checked-out tree **non-editable** (exactly what
`pip install git+https://github.com/Ickleslimer/codetalker.git` gives a stranger —
CI deliberately installs from the tree rather than the GitHub URL, which would test
the *previous* commit on push events), then runs `scripts/stranger_smoke.py`:

- stdio handshake + full tool catalog (core 8 tools present)
- v0.3 per-client instruction tailoring (freebuff mandate vs. short fallback)
- `codetalk_capabilities` answers, and its version matches the installed dist
- **empty-home probe**: with `HOME`/`USERPROFILE`/`APPDATA`/`XDG_*` redirected to
  an empty temp dir, capabilities and list answer gracefully (`count: 0`)

Run the same check locally against your editable install (skips the fresh-venv
step but exercises the identical assertions):

```bash
uv pip install . && python scripts/stranger_smoke.py
```

Or replicate CI exactly:

```bash
uv venv .smoke-venv --python 3.12
uv pip install --python .smoke-venv/Scripts/python.exe .   # bin/python on posix
.smoke-venv/Scripts/python.exe scripts/stranger_smoke.py
```
