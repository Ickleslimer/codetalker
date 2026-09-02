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
    StepPagination,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
)
from codetalker.utils.timestamps import timestamp_gte, timestamp_lte


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
        exclude_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = False,
        max_step_chars: int | None = None,
        offset: int = 0,
        from_end: bool = False,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        """Load and normalize steps for a given session with filtering applied."""
        pass

    def count_steps(self, session: NormalizedSession) -> int:
        """Return total step count for a session (may use a cheap peek when available)."""
        return len(
            self.load_steps(
                session=session,
                include_raw_data=False,
                include_thinking=True,
            )
        )

    def load_steps_paginated(
        self,
        session: NormalizedSession,
        since: str | None = None,
        until: str | None = None,
        since_last_user_input: bool = False,
        include_step_types: list[BlockType] | None = None,
        include_actor_roles: list[ActorRole] | None = None,
        exclude_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = False,
        max_step_chars: int | None = None,
        offset: int = 0,
        from_end: bool = False,
        limit: int | None = None,
    ) -> tuple[list[NormalizedStep], StepPagination]:
        """Load steps and return pagination metadata for the applied slice."""
        all_steps = self.load_steps(
            session=session,
            since=since,
            until=until,
            since_last_user_input=since_last_user_input,
            include_step_types=include_step_types,
            include_actor_roles=include_actor_roles,
            exclude_actor_roles=exclude_actor_roles,
            include_thinking=include_thinking,
            include_raw_data=include_raw_data,
            max_step_chars=max_step_chars,
            offset=0,
            from_end=False,
            limit=None,
        )
        return self.paginate_steps(
            all_steps, offset=offset, from_end=from_end, limit=limit
        )

    @staticmethod
    def paginate_steps(
        steps: Sequence[NormalizedStep],
        offset: int = 0,
        from_end: bool = False,
        limit: int | None = None,
    ) -> tuple[list[NormalizedStep], StepPagination]:
        total = len(steps)
        offset = max(0, offset)
        if total == 0:
            return [], StepPagination(
                offset=offset,
                limit=limit,
                from_end=from_end,
                returned_step_count=0,
                total_steps_available=0,
                has_more_before=False,
                has_more_after=False,
                next_offset=None,
            )

        if from_end:
            end_exclusive = max(0, total - offset)
            if limit is not None and limit > 0:
                start = max(0, end_exclusive - limit)
            else:
                start = 0
            sliced = list(steps[start:end_exclusive])
            has_more_before = start > 0
            has_more_after = offset > 0
            next_offset = offset + len(sliced) if has_more_before and sliced else None
            start_idx = sliced[0].step_index if sliced else None
            end_idx = sliced[-1].step_index if sliced else None
        else:
            start = min(offset, total)
            if limit is not None and limit > 0:
                sliced = list(steps[start : start + limit])
            else:
                sliced = list(steps[start:])
            end_exclusive = start + len(sliced)
            has_more_before = start > 0
            has_more_after = end_exclusive < total
            next_offset = end_exclusive if has_more_after else None
            start_idx = sliced[0].step_index if sliced else None
            end_idx = sliced[-1].step_index if sliced else None

        return sliced, StepPagination(
            offset=offset,
            limit=limit,
            from_end=from_end,
            returned_step_count=len(sliced),
            total_steps_available=total,
            has_more_before=has_more_before,
            has_more_after=has_more_after,
            next_offset=next_offset,
            start_step_index=start_idx,
            end_step_index=end_idx,
        )

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

        active_branch = next(
            (s for s in matching_branches if s.branch_root_step_id is None),
            matching_branches[0],
        )

        display_name = active_branch.display_name or f"{self.harness_name} {conversation_id[:8]}"
        branches_summary: list[BranchSummary] = []
        fork_map: dict[str, list[str]] = {}
        child_subagents: list[str] = []

        for s in matching_branches:
            is_active = s.session_id == active_branch.session_id
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
        summary_only: bool = True,
        include_raw_data: bool = False,
        limit_per_branch: int = 20,
        from_end: bool = True,
    ) -> BranchDiff | None:
        """Compute step divergence between two branches of a conversation."""
        sessions = self.discover_sessions(root_path=root_path)
        matching_map = {
            s.session_id: s
            for s in sessions
            if s.conversation_id == conversation_id or s.session_id == conversation_id
        }

        sess_a = matching_map.get(branch_a)
        sess_b = matching_map.get(branch_b)

        if not sess_a:
            sess_a = next((s for s in sessions if s.session_id == branch_a), None)
        if not sess_b:
            sess_b = next((s for s in sessions if s.session_id == branch_b), None)

        if not sess_a or not sess_b:
            return None

        steps_a = self.load_steps(sess_a, include_raw_data=include_raw_data)
        steps_b = self.load_steps(sess_b, include_raw_data=include_raw_data)

        common_len = 0
        min_len = min(len(steps_a), len(steps_b))

        for i in range(min_len):
            sa = steps_a[i]
            sb = steps_b[i]
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

        last_common = common_steps[-1] if common_steps else None
        divergence_step_id = last_common.branch.step_id if last_common and last_common.branch else None
        divergence_index = common_len - 1 if common_len > 0 else None

        if summary_only:
            common_steps = []
            if from_end and limit_per_branch > 0:
                distinct_a = distinct_a[-limit_per_branch:]
                distinct_b = distinct_b[-limit_per_branch:]
            elif limit_per_branch > 0:
                distinct_a = distinct_a[:limit_per_branch]
                distinct_b = distinct_b[:limit_per_branch]
        else:
            if from_end and limit_per_branch > 0:
                distinct_a = distinct_a[-limit_per_branch:]
                distinct_b = distinct_b[-limit_per_branch:]
            elif limit_per_branch > 0:
                distinct_a = distinct_a[:limit_per_branch]
                distinct_b = distinct_b[:limit_per_branch]

        return BranchDiff(
            conversation_id=conversation_id,
            harness=self.harness_name,
            branch_a_id=branch_a,
            branch_b_id=branch_b,
            divergence_step_id=divergence_step_id,
            divergence_step_index=divergence_index,
            common_step_count=common_len,
            branch_a_distinct_step_count=len(steps_a) - common_len,
            branch_b_distinct_step_count=len(steps_b) - common_len,
            common_steps=common_steps,
            branch_a_distinct_steps=distinct_a,
            branch_b_distinct_steps=distinct_b,
            summary_only=summary_only,
        )

    @staticmethod
    def _truncate_block_text(text: str, max_chars: int) -> tuple[str, bool]:
        if len(text) <= max_chars:
            return text, False
        if max_chars <= 1:
            return "…", True
        return text[: max_chars - 1] + "…", True

    @staticmethod
    def filter_normalized_steps(
        steps: Sequence[NormalizedStep],
        since: str | None = None,
        until: str | None = None,
        since_last_user_input: bool = False,
        include_step_types: list[BlockType] | None = None,
        include_actor_roles: list[ActorRole] | None = None,
        exclude_actor_roles: list[ActorRole] | None = None,
        include_thinking: bool = True,
        include_raw_data: bool = False,
        max_step_chars: int | None = None,
        offset: int = 0,
        from_end: bool = False,
        limit: int | None = None,
    ) -> list[NormalizedStep]:
        """Standard filter implementation for a list of normalized steps."""
        filtered: list[NormalizedStep] = list(steps)

        if since_last_user_input:
            last_user_idx = -1
            for idx, step in enumerate(filtered):
                if step.actor.role == ActorRole.USER:
                    last_user_idx = idx
            if last_user_idx != -1:
                filtered = filtered[last_user_idx:]

        if since is not None:
            filtered = [
                s for s in filtered if s.timestamp is None or timestamp_gte(s.timestamp, since)
            ]
        if until is not None:
            filtered = [
                s for s in filtered if s.timestamp is None or timestamp_lte(s.timestamp, until)
            ]

        if include_actor_roles is not None:
            role_set = set(include_actor_roles)
            filtered = [s for s in filtered if s.actor.role in role_set]

        if exclude_actor_roles is not None:
            excluded = set(exclude_actor_roles)
            filtered = [s for s in filtered if s.actor.role not in excluded]

        result: list[NormalizedStep] = []
        for step in filtered:
            step_blocks = list(step.blocks)

            if not include_thinking:
                step_blocks = [b for b in step_blocks if b.type != BlockType.THINKING]

            if include_step_types is not None:
                type_set = set(include_step_types)
                step_blocks = [b for b in step_blocks if b.type in type_set]

            if max_step_chars is not None and max_step_chars > 0:
                clipped_blocks = []
                for block in step_blocks:
                    if isinstance(block, (TextBlock, ThinkingBlock)):
                        new_text, truncated = BaseAdapter._truncate_block_text(
                            block.text, max_step_chars
                        )
                        clipped_blocks.append(
                            block.model_copy(
                                update={"text": new_text, "is_truncated": truncated or block.is_truncated}
                            )
                        )
                    elif isinstance(block, ToolResultBlock):
                        new_content, truncated = BaseAdapter._truncate_block_text(
                            block.content, max_step_chars
                        )
                        clipped_blocks.append(
                            block.model_copy(
                                update={
                                    "content": new_content,
                                    "is_truncated": truncated or block.is_truncated,
                                }
                            )
                        )
                    else:
                        clipped_blocks.append(block)
                step_blocks = clipped_blocks

            if step.blocks and not step_blocks:
                continue

            has_sig = any(
                isinstance(b, ThinkingBlock) and b.has_signature for b in step_blocks
            )
            raw = step.raw_data if (include_raw_data or has_sig) else {}

            result.append(
                step.model_copy(
                    update={
                        "blocks": step_blocks,
                        "raw_data": raw,
                    }
                )
            )

        sliced, _ = BaseAdapter.paginate_steps(
            result, offset=offset, from_end=from_end, limit=limit
        )
        return sliced
