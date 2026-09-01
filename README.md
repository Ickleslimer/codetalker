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
| `codetalk_capabilities` | _(none)_ | List harnesses, aliases, ID guidance, and recommended read defaults. Call once per agent session. |
| `codetalk_list` | `harness`, `conversation_id`, `since`, `limit`, `root_path`, `include_capabilities`, `include_harness_status` | List sessions (slim by default). Returns `harness_status` when listing all harnesses. |
| `codetalk_read` | `session_id`, `harness`, `since`, `until`, `since_last_user_input`, `conversation_only`, `exclude_actor_roles`, `include_thinking`, `include_raw_data`, `max_step_chars`, `offset`, `from_end`, `limit`, `root_path` | Read normalized steps. Defaults: tail slice (`from_end=true`), conversation-only (`conversation_only=true`), no raw payloads (`include_raw_data=false`). |
| `codetalk_branches` | `conversation_id`, `harness`, `root_path` | DAG branch tree, fork points, and subagent hierarchy (`branch_id` usually equals `session_id`). |
| `codetalk_diff_branches` | `conversation_id`, `branch_a`, `branch_b`, `harness`, `summary_only`, `include_raw_data`, `limit_per_branch`, `from_end`, `root_path` | Compare branches. Defaults to `summary_only=true` (counts/metadata only). |
| `codetalk_filter` | `session_id`, `harness`, `keywords`, `step_types`, `actor_roles`, `conversation_only`, `exclude_actor_roles`, `since_last_user_input`, `include_thinking`, `include_raw_data`, `max_step_chars`, `offset`, `from_end`, `limit`, `root_path` | Filter steps by keywords, types, or roles. |
| `codetalk_search` | `query`, `harness`, `since`, `limit`, `max_sessions_to_search`, `root_path` | Search titles and recent transcript tails (includes `step_index`). |
| `codetalk_info` | `session_id`, `harness`, `root_path` | Fast metadata without step bodies (refreshes step counts when possible). |

### Agent quickstart

1. `codetalk_capabilities` — learn harness names, aliases (`codex` → `chatgpt`), and ID fields.
2. `codetalk_list` — find recent sessions (`harness_status` explains empty harnesses).
3. `codetalk_read` with defaults — tail of conversation without system injections or `raw_data`.
4. `codetalk_branches` when `has_dag=true` on a listed session.

Note: Codex CLI rollouts appear under harness `chatgpt`; use `session_id` for reads and `conversation_id` for branch tools.

---

## Installation & Setup

### Running locally
```bash
uv sync
uv run pytest -v
uv run codetalker --log-level INFO
```

### Adding to MCP Configuration

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
