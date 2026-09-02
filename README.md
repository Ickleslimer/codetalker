# CodeTalker

> Cross-harness agent conversation transcript normalizer and MCP server.

CodeTalker is an agent-callable tool and MCP server that normalizes conversation transcripts from different AI coding harnesses into a unified schema. This allows any agent to pick up context, search past decisions, or read thread history without requiring manual handoff documents.

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

---

## Installation & Setup

### Running locally
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
