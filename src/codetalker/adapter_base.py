from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from codetalker.schema import (
    ActorRole,
    BlockType,
    BranchDiff,
    BranchSummary,
    ConversationBranchTree,
    ForkPoint,
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

    def get_branch_tree(
        self,
        conversation_id: str,
        root_path: str | None = None,
    ) -> ConversationBranchTree | None:
        """Construct the branch/DAG tree for a given conversation ID."""
        sessions = self.discover_sessions(root_path=root_path)
        matching_branches = [
            s
            for s in sessions
            if s.conversation_id == conversation_id or s.session_id == conversation_id
        ]
        if not matching_branches:
            return None

        # Determine primary / active branch
        active_branch = next(
            (s for s in matching_branches if s.branch_root_step_id is None),
            matching_branches[0],
        )

        display_name = active_branch.display_name or f"{self.harness_name} {conversation_id[:8]}"
        branches_summary: list[BranchSummary] = []
        fork_map: dict[str, list[str]] = {}
        child_subagents: list[str] = []

        for s in matching_branches:
            is_active = (s.session_id == active_branch.session_id)
            branches_summary.append(
                BranchSummary(
                    branch_id=s.session_id,
                    branch_label=s.branch_label or ("Main Thread" if is_active else "Branch"),
                    divergence_step_id=s.branch_root_step_id,
                    leaf_step_id=s.active_node_id or s.session_id,
                    step_count=s.step_count,
                    user_turn_count=s.user_turn_count,
                    assistant_turn_count=s.assistant_turn_count,
                    model=s.model,
                    is_active_path=is_active,
                    started_at=s.started_at,
                    last_activity=s.last_activity,
                )
            )
            if s.branch_root_step_id:
                fork_map.setdefault(s.branch_root_step_id, []).append(s.session_id)
            if s.child_session_ids:
                child_subagents.extend(s.child_session_ids)

        fork_points: list[ForkPoint] = []
        for step_id, b_ids in fork_map.items():
            fork_points.append(
                ForkPoint(
                    step_id=step_id,
                    variant_count=len(b_ids) + 1,
                    branch_ids=b_ids,
                )
            )

        return ConversationBranchTree(
            conversation_id=conversation_id,
            harness=self.harness_name,
            display_name=display_name,
            active_branch_id=active_branch.session_id,
            branch_count=len(matching_branches),
            branches=branches_summary,
            fork_points=fork_points,
            child_subagent_sessions=list(set(child_subagents)),
            has_dag=len(matching_branches) > 1 or any(s.has_dag for s in matching_branches),
        )

    def diff_branches(
        self,
        conversation_id: str,
        branch_a: str,
        branch_b: str,
        root_path: str | None = None,
    ) -> BranchDiff | None:
        """Compute the step divergence and distinct turns between two branches of a conversation."""
        sessions = self.discover_sessions(root_path=root_path)
        matching_map = {
            s.session_id: s
            for s in sessions
            if s.conversation_id == conversation_id or s.session_id == conversation_id
        }

        sess_a = matching_map.get(branch_a)
        sess_b = matching_map.get(branch_b)

        # Fallback search across all discovered sessions
        if not sess_a:
            sess_a = next((s for s in sessions if s.session_id == branch_a), None)
        if not sess_b:
            sess_b = next((s for s in sessions if s.session_id == branch_b), None)

        if not sess_a or not sess_b:
            return None

        steps_a = self.load_steps(sess_a)
        steps_b = self.load_steps(sess_b)

        # Find common prefix length
        common_len = 0
        min_len = min(len(steps_a), len(steps_b))

        for i in range(min_len):
            sa = steps_a[i]
            sb = steps_b[i]
            # Match by step_id in branch or by content equivalence
            if sa.branch and sb.branch and sa.branch.step_id == sb.branch.step_id:
                common_len += 1
            elif (
                sa.actor.role == sb.actor.role
                and len(sa.blocks) == len(sb.blocks)
                and sa.model_dump(exclude={"timestamp", "branch", "raw_data"})
                == sb.model_dump(exclude={"timestamp", "branch", "raw_data"})
            ):
                common_len += 1
            else:
                break

        common_steps = steps_a[:common_len]
        distinct_a = steps_a[common_len:]
        distinct_b = steps_b[common_len:]

        divergence_step_id = common_steps[-1].branch.step_id if common_steps and common_steps[-1].branch else None
        divergence_index = common_len - 1 if common_len > 0 else None

        return BranchDiff(
            conversation_id=conversation_id,
            harness=self.harness_name,
            branch_a_id=branch_a,
            branch_b_id=branch_b,
            divergence_step_id=divergence_step_id,
            divergence_step_index=divergence_index,
            common_step_count=len(common_steps),
            branch_a_distinct_step_count=len(distinct_a),
            branch_b_distinct_step_count=len(distinct_b),
            common_steps=common_steps,
            branch_a_distinct_steps=distinct_a,
            branch_b_distinct_steps=distinct_b,
        )

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
