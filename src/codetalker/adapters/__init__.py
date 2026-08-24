from codetalker.adapters.aider import AiderAdapter
from codetalker.adapters.antigravity import AntigravityAdapter
from codetalker.adapters.chatgpt import ChatGPTAdapter
from codetalker.adapters.claude import ClaudeCodeAdapter
from codetalker.adapters.copilot import GitHubCopilotAdapter
from codetalker.adapters.cursor import CursorAdapter
from codetalker.adapters.freebuff import FreebuffAdapter
from codetalker.adapters.opencode import OpenCodeAdapter
from codetalker.adapters.windsurf import WindsurfAdapter
from codetalker.registry import registry

# 1. ChatGPT / Codex adapter
registry.register(
    ChatGPTAdapter(),
    aliases=["codex", "chatgpt_desktop", "chatgpt.exe", "openai", "openai_codex", "codex_cli", "chat_gpt"],
)

# 2. Devin / Windsurf adapter
registry.register(
    WindsurfAdapter(),
    aliases=["devin", "codeium", "windsurf_ide", "windsurf-ide", "windsurf"],
)

# 3. Antigravity adapter
registry.register(
    AntigravityAdapter(),
    aliases=["agy", "gemini", "google_antigravity", "antigravity_ide", "antigravity-ide", "google-antigravity", "google_antigravity_ide"],
)

# 4. Cursor adapter
registry.register(
    CursorAdapter(),
    aliases=["cursor_ide", "cursor-ide", "anysphere"],
)

# 5. Claude Code adapter
registry.register(
    ClaudeCodeAdapter(),
    aliases=["claudecode", "claude_code", "claude-code", "anthropic", "claude"],
)

# 6. Aider adapter
registry.register(
    AiderAdapter(),
    aliases=["aider_chat", "aider-chat"],
)

# 7. GitHub Copilot adapter
registry.register(
    GitHubCopilotAdapter(),
    aliases=["github_copilot", "copilot_chat", "vscode_copilot", "github-copilot"],
)

# 8. Freebuff / Codebuff adapter
registry.register(
    FreebuffAdapter(),
    aliases=["codebuff", "freebuff_desktop", "freebuff-desktop", "free_buff"],
)

# 9. OpenCode adapter
registry.register(
    OpenCodeAdapter(),
    aliases=["open_code", "opencode_desktop", "opencode-ai", "open-code"],
)

__all__ = [
    "ChatGPTAdapter",
    "WindsurfAdapter",
    "AntigravityAdapter",
    "CursorAdapter",
    "ClaudeCodeAdapter",
    "AiderAdapter",
    "GitHubCopilotAdapter",
    "FreebuffAdapter",
    "OpenCodeAdapter",
]
