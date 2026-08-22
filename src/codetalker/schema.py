from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field


# ─── Enumerations ─────────────────────────────────────────────────────────────

class BlockType(str, Enum):
    TEXT         = "text"          # Plain text or markdown prose
    THINKING     = "thinking"      # Model's internal reasoning (pre-response)
    TOOL_CALL    = "tool_call"     # Agent invoked a tool / function
    TOOL_RESULT  = "tool_result"   # Tool returned a result
    CODE_DIFF    = "code_diff"     # A file edit recorded as a diff (Cursor, Aider)
    ATTACHMENT   = "attachment"    # File, image, document, or binary reference
    APPROVAL     = "approval"      # Human approved/rejected an action (Codex)
    SYSTEM_EVENT = "system_event"  # Harness-emitted: checkpoint, session meta, etc.


class ActorRole(str, Enum):
    USER      = "user"       # Human / end-user input
    ASSISTANT = "assistant"  # Model / agent response
    SYSTEM    = "system"     # Harness or infrastructure
    TOOL      = "tool"       # Tool/function return (if modelled as separate actor)


class ApprovalOutcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING  = "pending"
    AUTO     = "auto"        # Auto-approved without human review


class DiffStatus(str, Enum):
    APPLIED  = "applied"     # Change was written to disk
    REJECTED = "rejected"    # Change was not applied
    PENDING  = "pending"     # Proposed but not yet accepted


# ─── Content Blocks ───────────────────────────────────────────────────────────

class TextBlock(BaseModel):
    type: Literal[BlockType.TEXT] = BlockType.TEXT
    text: str
    is_truncated: bool = False


class ThinkingBlock(BaseModel):
    type: Literal[BlockType.THINKING] = BlockType.THINKING
    text: str
    is_truncated: bool = False
    has_signature: bool = False


class AttachmentBlock(BaseModel):
    type: Literal[BlockType.ATTACHMENT] = BlockType.ATTACHMENT
    attachment_type: str           # "file", "image", "document", "url", "asset"
    name: str | None = None        # Filename or label
    path: str | None = None        # Absolute or relative path on disk (if local)
    url: str | None = None         # Remote URL or asset pointer (ChatGPT asset IDs etc.)
    media_type: str | None = None  # MIME type
    line_range: tuple[int, int] | None = None  # Referenced line range within file
    inline_content: str | None = None          # Small files may be embedded


class ToolCallBlock(BaseModel):
    type: Literal[BlockType.TOOL_CALL] = BlockType.TOOL_CALL
    tool_call_id: str | None = None  # ID used to pair with ToolResultBlock
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    args_truncated: bool = False


class ToolResultBlock(BaseModel):
    type: Literal[BlockType.TOOL_RESULT] = BlockType.TOOL_RESULT
    tool_call_id: str | None = None  # Matches ToolCallBlock.tool_call_id
    tool_name: str | None = None     # Denormalized for convenience
    content: str                     # Stringified result text
    is_error: bool = False
    is_truncated: bool = False
    artifacts: list[AttachmentBlock] = Field(default_factory=list)


class CodeDiffBlock(BaseModel):
    type: Literal[BlockType.CODE_DIFF] = BlockType.CODE_DIFF
    file_uri: str                  # Absolute path or file:// URI
    diff: str | None = None        # Unified diff format if available
    original_content: str | None = None
    modified_content: str | None = None
    status: DiffStatus = DiffStatus.PENDING
    line_range: tuple[int, int] | None = None


class ApprovalBlock(BaseModel):
    type: Literal[BlockType.APPROVAL] = BlockType.APPROVAL
    outcome: ApprovalOutcome
    action_description: str | None = None
    tool_call_id: str | None = None


class SystemEventBlock(BaseModel):
    type: Literal[BlockType.SYSTEM_EVENT] = BlockType.SYSTEM_EVENT
    event_name: str
    detail: str | None = None


ContentBlock = Annotated[
    Union[
        TextBlock,
        ThinkingBlock,
        ToolCallBlock,
        ToolResultBlock,
        CodeDiffBlock,
        AttachmentBlock,
        ApprovalBlock,
        SystemEventBlock,
    ],
    Field(discriminator="type"),
]


# ─── Actor ────────────────────────────────────────────────────────────────────

class Actor(BaseModel):
    role: ActorRole
    agent_id: str | None = None
    agent_name: str | None = None
    model: str | None = None
    parent_agent_id: str | None = None


# ─── Branch Metadata ──────────────────────────────────────────────────────────

class BranchInfo(BaseModel):
    step_id: str
    parent_step_id: str | None = None
    children_step_ids: list[str] = Field(default_factory=list)
    is_on_active_path: bool = True


# ─── Step ─────────────────────────────────────────────────────────────────────

class NormalizedStep(BaseModel):
    step_index: int
    timestamp: str | None = None
    actor: Actor
    blocks: list[ContentBlock] = Field(default_factory=list)

    branch: BranchInfo | None = None
    spawned_session_id: str | None = None
    parent_session_id: str | None = None

    status: str | None = None
    error: str | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    finish_reason: str | None = None

    raw_data: dict[str, Any] = Field(default_factory=dict)
    harness_step_type: str | None = None


# ─── Branch Tree Models ───────────────────────────────────────────────────────

class BranchSummary(BaseModel):
    branch_id: str
    branch_label: str
    divergence_step_id: str | None = None
    leaf_step_id: str | None = None
    step_count: int = 0
    user_turn_count: int = 0
    assistant_turn_count: int = 0
    model: str | None = None
    is_active_path: bool = False
    started_at: str | None = None
    last_activity: str | None = None


class ForkPoint(BaseModel):
    step_id: str
    step_index: int | None = None
    prompt_preview: str | None = None
    variant_count: int = 0
    branch_ids: list[str] = Field(default_factory=list)


class ConversationBranchTree(BaseModel):
    conversation_id: str
    harness: str
    display_name: str | None = None
    active_branch_id: str | None = None
    branch_count: int = 0
    branches: list[BranchSummary] = Field(default_factory=list)
    fork_points: list[ForkPoint] = Field(default_factory=list)
    child_subagent_sessions: list[str] = Field(default_factory=list)
    has_dag: bool = False


class BranchDiff(BaseModel):
    conversation_id: str
    harness: str
    branch_a_id: str
    branch_b_id: str
    divergence_step_id: str | None = None
    divergence_step_index: int | None = None
    common_step_count: int = 0
    branch_a_distinct_step_count: int = 0
    branch_b_distinct_step_count: int = 0
    common_steps: list[NormalizedStep] = Field(default_factory=list)
    branch_a_distinct_steps: list[NormalizedStep] = Field(default_factory=list)
    branch_b_distinct_steps: list[NormalizedStep] = Field(default_factory=list)


# ─── Session ──────────────────────────────────────────────────────────────────

class NormalizedSession(BaseModel):
    session_id: str
    harness: str
    display_name: str | None = None

    conversation_id: str | None = None
    branch_root_step_id: str | None = None
    branch_label: str | None = None

    started_at: str | None = None
    last_activity: str | None = None

    working_directory: str | None = None
    model: str | None = None
    git_branch: str | None = None

    step_count: int = 0
    user_turn_count: int = 0
    assistant_turn_count: int = 0

    source_path: str
    source_format: str

    active_node_id: str | None = None
    steps: list[NormalizedStep] = Field(default_factory=list)

    child_session_ids: list[str] = Field(default_factory=list)
    parent_session_id: str | None = None

    is_truncated: bool = False
    requires_reducer: bool = False
    has_dag: bool = False
