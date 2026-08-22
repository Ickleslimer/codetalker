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
registry.register(ChatGPTAdapter(), aliases=["codex"])

# 2. Devin / Windsurf adapter
registry.register(WindsurfAdapter(), aliases=["devin"])

# 3. Antigravity adapter
registry.register(AntigravityAdapter(), aliases=["agy"])

# 4. Cursor adapter
registry.register(CursorAdapter())

# 5. Claude Code adapter
registry.register(ClaudeCodeAdapter(), aliases=["claudecode"])

# 6. Aider adapter
registry.register(AiderAdapter())

# 7. GitHub Copilot adapter
registry.register(GitHubCopilotAdapter(), aliases=["github_copilot"])

# 8. Freebuff / Codebuff adapter
registry.register(FreebuffAdapter(), aliases=["codebuff"])

# 9. OpenCode adapter
registry.register(OpenCodeAdapter(), aliases=["open_code"])

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
