from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from codetalker.schema import (
    ActorRole,
    BlockType,
    NormalizedSession,
    NormalizedStep,
    ThinkingBlock,
)


class BaseAdapter(ABC):
    """Abstract base class for all harness adapters."""

    harness_name: str

    @abstractmethod
    def discover_sessions(
        self,
        root_path: str | None = None,
    ) -> list[NormalizedSession]:
        """Discover and return session shells (metadata only, without full steps)."""
        pass

    @abstractmethod
    def load_steps(
        self,
        session: NormalizedSession,
        since: str | None = None,
        until: str | None = None,
        since_last_user_input: bool = False,
        include_step_types: list[BlockType] | None = None,
        include_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = True,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        """Load and normalize steps for a given session with filtering applied."""
        pass

    @staticmethod
    def filter_normalized_steps(
        steps: Sequence[NormalizedStep],
        since: str | None = None,
        until: str | None = None,
        since_last_user_input: bool = False,
        include_step_types: list[BlockType] | None = None,
        include_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = True,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        """Standard filter implementation for a list of normalized steps."""
        filtered: list[NormalizedStep] = list(steps)

        # 1. Filter by since_last_user_input: slice from the last USER turn onward
        if since_last_user_input:
            last_user_idx = -1
            for idx, step in enumerate(filtered):
                if step.actor.role == ActorRole.USER:
                    last_user_idx = idx
            if last_user_idx != -1:
                filtered = filtered[last_user_idx:]

        # 2. Filter by ISO timestamp boundaries
        if since is not None:
            filtered = [s for s in filtered if s.timestamp is None or s.timestamp >= since]
        if until is not None:
            filtered = [s for s in filtered if s.timestamp is None or s.timestamp <= until]

        # 3. Filter by actor roles
        if include_actor_roles is not None:
            role_set = set(include_actor_roles)
            filtered = [s for s in filtered if s.actor.role in role_set]

        # 4. Filter blocks inside each step (thinking, step_types)
        result: list[NormalizedStep] = []
        for step in filtered:
            # Clone step blocks
            step_blocks = list(step.blocks)

            if not include_thinking:
                step_blocks = [b for b in step_blocks if b.type != BlockType.THINKING]

            if include_step_types is not None:
                type_set = set(include_step_types)
                step_blocks = [b for b in step_blocks if b.type in type_set]

            # If block filtering removed all blocks, only keep step if original had no blocks
            if step.blocks and not step_blocks:
                continue

            has_sig = any(
                isinstance(b, ThinkingBlock) and b.has_signature for b in step_blocks
            )
            raw = step.raw_data if (include_raw_data or has_sig) else {}

            new_step = step.model_copy(
                update={
                    "blocks": step_blocks,
                    "raw_data": raw,
                }
            )
            result.append(new_step)

        # 5. Apply limit
        if limit is not None and limit > 0:
            result = result[:limit]

        return result
