"""Measure when codetalk_recover actually fired — v0.2 baseline, rerunnable.

For every recorded session on every harness with local data, find each
``codetalk_recover`` tool call and classify the turn it ran in:

  wipe    — a harness wipe marker (``RESTART_MARKERS``) exists at or before
            the call in that session's transcript (recovery was warranted).
  healthy — no marker anywhere behind the call (recovery ran on a turn whose
            context was, as far as the transcript shows, intact).

Markers behind the call are scanned over the WHOLE transcript, not a tail
window — that can only move calls from "healthy" to "wipe", so the healthy
count is conservative (never overstates waste). Calls are grouped into turns
(assistant steps between two user steps) so a retried recover counts once.

Era split: v0.3 shipped 2026-09-19; calls on/after that date are bucketed
separately so the same script produces the after-number later.

Output: .audit/recover-baseline.json next to the audit tooling's own output.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import codetalker.adapters  # noqa: F401  (registers adapters on import)
from codetalker.continuity import RESTART_MARKERS
from codetalker.registry import registry
from codetalker.schema import ActorRole, NormalizedStep, ToolCallBlock
from codetalker.utils.timestamps import timestamp_gte

# Persisted wipe PROXIES for harnesses that strip the runtime markers
# (Freebuff drops <failed_turn>/<since_your_last_turn> before writing the
# message row). The auto-resume sentinel is the only user-visible artifact
# Freebuff persists for a failed turn — and it lands AFTER the failed turn,
# so it is confirmation, never an a-priori trigger.
PERSISTED_WIPE_PROXIES: tuple[str, ...] = (
    "Continue the interrupted request from where you left off.",
    "<since_your_last_turn>",
    "<failed_turn>",
)

# Phrases a wiped agent writes when justifying a recover call from the
# antecedent check — the only runtime-truth signal that survives persistence.
JUSTIFICATION_PHRASES: tuple[str, ...] = (
    "can't see in current context",
    "cannot see in current context",
    "not in my context",
    "no visible antecedent",
)

RECOVER_TOOLS = {"codetalk_recover"}
# Some harnesses record MCP calls under their own dispatcher name with the
# real tool inside the args (Freebuff: call_mcp_tool), and some namespace
# MCP tool names (mcp__codetalker__codetalk_recover).
DISPATCHER_TOOLS = {"call_mcp_tool"}
# codetalker v0.2 (codetalk_recover + per-turn mandate) shipped in afbfd36:
# 2026-09-18 16:11:37 +0100 == 15:11Z. Recover calls cannot predate it.
V02_SHIP_UTC = "2026-09-18T15:00:00+00:00"
V03_SHIP_DATE = "2026-09-19"  # calls on/after this date are v0.3-era
PAGE_SIZE = 1000
TIME_BUDGET_SECONDS = 240.0
# Measure the harnesses most likely to have codetalker connected first.
HARNESS_PRIORITY = ["freebuff", "cursor", "antigravity", "chatgpt"]
OUT_PATH = Path(__file__).resolve().parents[1] / ".audit" / "recover-baseline.json"


def load_all_steps(adapter, sess) -> list[NormalizedStep]:
    """Full transcript via pagination (load_steps caps by default)."""
    all_steps: list[NormalizedStep] = []
    offset = 0
    while True:
        chunk = adapter.load_steps(
            session=sess,
            offset=offset,
            from_end=False,
            limit=PAGE_SIZE,
            include_raw_data=False,
            include_thinking=False,
        )
        if not chunk:
            break
        all_steps.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        offset += len(chunk)
    return all_steps


def step_text(step: NormalizedStep) -> str:
    """TEXT-block content only — mirrors continuity.extract_step_text."""
    from codetalker.schema import BlockType

    parts = []
    for block in step.blocks:
        if getattr(block, "type", None) is BlockType.TEXT:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def is_wipe_marker(step: NormalizedStep) -> bool:
    """Persisted-marker check, mirroring detect_session_fresh's role discipline.

    Extends the runtime marker list with the persisted sentinel proxy — on
    Freebuff the real markers never reach disk, only this does (after the
    fact).
    """
    if step.actor.role not in (ActorRole.USER, ActorRole.SYSTEM):
        return False
    text = step_text(step)
    return any(
        marker in text for marker in RESTART_MARKERS + PERSISTED_WIPE_PROXIES
    )


def _is_recover_block(block) -> bool:
    if not isinstance(block, ToolCallBlock):
        return False
    name = block.tool_name or ""
    if name in RECOVER_TOOLS or any(t in name for t in RECOVER_TOOLS):
        return True
    if name in DISPATCHER_TOOLS and isinstance(block.tool_args, dict):
        inner = str(block.tool_args.get("tool") or "")
        return any(t == inner for t in RECOVER_TOOLS)
    return False


def find_recover_calls(steps: list[NormalizedStep]) -> list[int]:
    """Step indices holding a codetalk_recover call (any recording shape)."""
    out = []
    for step in steps:
        if any(_is_recover_block(b) for b in step.blocks):
            out.append(step.step_index)
    return out


def turn_boundaries(steps: list[NormalizedStep]) -> list[int]:
    """Indices of user steps — each starts a turn."""
    return [
        s.step_index
        for s in steps
        if s.actor.role == ActorRole.USER
    ]


def classify(steps: list[NormalizedStep], call_idx: int) -> dict:
    """Classify one recover call; returns record for the report."""
    # marker positions at or before the call (marker <= call — a later
    # restart cannot explain this call)
    marker_positions = [
        s.step_index for s in steps if s.step_index <= call_idx and is_wipe_marker(s)
    ]
    # runtime-truth signal: the agent's own persisted justification text
    call_step = next((s for s in steps if s.step_index == call_idx), None)
    call_text = step_text(call_step) if call_step is not None else ""
    justified = any(p in call_text for p in JUSTIFICATION_PHRASES)
    if marker_positions:
        nearest = max(marker_positions)
        return {
            "classification": "wipe",
            "runtime_justified": justified,
            "call_time": call_step.timestamp if call_step is not None else None,
            "call_step": call_idx,
            "nearest_marker_step": nearest,
            "steps_since_marker": call_idx - nearest,
        }
    return {
        "classification": "healthy",
        "runtime_justified": justified,
        "call_time": call_step.timestamp if call_step is not None else None,
        "call_step": call_idx,
        "nearest_marker_step": None,
        "steps_since_marker": None,
    }


def main() -> int:
    started = time.monotonic()

    def budget_left() -> float:
        return TIME_BUDGET_SECONDS - (time.monotonic() - started)

    def log(msg: str) -> None:
        print(msg, flush=True)

    sessions_scanned = 0
    sessions_skipped_window = 0
    sessions_with_recover = 0
    sessions_with_markers = 0
    step_load_failures = 0
    calls: list[dict] = []
    calls_by_harness: Counter = Counter()
    healthy_by_harness: Counter = Counter()
    wipe_by_harness: Counter = Counter()
    calls_by_era: Counter = Counter()
    healthy_by_era: Counter = Counter()
    turn_dedupe_savings = 0  # extra recover calls inside already-counted turns
    budget_exhausted = False

    names = list(registry.list_canonical_harnesses())
    names.sort(key=lambda h: HARNESS_PRIORITY.index(h) if h in HARNESS_PRIORITY else 99)

    for harness in names:
        adapter = registry.get(harness)
        if adapter is None:
            continue
        try:
            sessions = adapter.discover_sessions()
        except Exception:
            continue
        # v0.2's recover tool cannot exist in older sessions — skip them.
        in_window = [
            s
            for s in sessions
            if timestamp_gte(s.last_activity or s.started_at or "", V02_SHIP_UTC)
            or timestamp_gte(s.started_at or s.last_activity or "", V02_SHIP_UTC)
        ]
        sessions_skipped_window += len(sessions) - len(in_window)
        log(f"[{harness}] {len(in_window)} sessions in v0.2 window "
            f"({len(sessions) - len(in_window)} older skipped), budget {budget_left():.0f}s")
        for sess in in_window:
            if budget_left() <= 0:
                budget_exhausted = True
                break
            try:
                steps = load_all_steps(adapter, sess)
            except Exception:
                step_load_failures += 1
                continue
            if not steps:
                continue
            sessions_scanned += 1

            recover_idx = find_recover_calls(steps)
            user_starts = turn_boundaries(steps)
            marker_steps = [s.step_index for s in steps if is_wipe_marker(s)]
            if marker_steps:
                sessions_with_markers += 1
            if not recover_idx:
                continue
            sessions_with_recover += 1

            # dedupe: calls already attributed to a counted turn
            counted_turns: set[int] = set()
            for call_idx in sorted(recover_idx):
                prior_turns = [t for t in user_starts if t <= call_idx]
                turn_start = prior_turns[-1] if prior_turns else -1
                if turn_start in counted_turns:
                    turn_dedupe_savings += 1
                    continue
                counted_turns.add(turn_start)
                rec = classify(steps, call_idx)
                rec.update(
                    {
                        "harness": harness,
                        "session_id": sess.session_id,
                        "session_last_activity": sess.last_activity,
                        "era": (
                            "v0.3"
                            if (rec.get("call_time") or "") >= V03_SHIP_DATE
                            else "v0.2"
                        ),
                        "session_marker_count": len(marker_steps),
                    }
                )
                calls.append(rec)
                calls_by_harness[harness] += 1
                calls_by_era[rec["era"]] += 1
                if rec["classification"] == "healthy":
                    healthy_by_harness[harness] += 1
                    healthy_by_era[rec["era"]] += 1
                else:
                    wipe_by_harness[harness] += 1

    healthy_calls = [c for c in calls if c["classification"] == "healthy"]
    wipe_calls = [c for c in calls if c["classification"] == "wipe"]
    dist_steps_since_marker = Counter(
        c["steps_since_marker"] for c in wipe_calls
    )

    report = {
        "what": "codetalk_recover trigger classification (v0.2 baseline)",
        "v03_ship_date": V03_SHIP_DATE,
        "totals": {
            "sessions_scanned": sessions_scanned,
            "sessions_with_recover_calls": sessions_with_recover,
            "sessions_with_wipe_markers": sessions_with_markers,
            "step_load_failures": step_load_failures,
            "recover_calls_total": len(calls),
            "recover_turns_total": len(calls) + turn_dedupe_savings,
            "recover_turns_deduped_extra_calls": turn_dedupe_savings,
            "healthy_turn_recover_calls": len(healthy_calls),
            "wipe_turn_recover_calls": len(wipe_calls),
        },
        "headline": (
            "healthy-turn recover calls / all recover turns "
            f"= {len(healthy_calls)}/{len(calls) + turn_dedupe_savings}"
        ),
        "by_era": {
            era: {
                "recover_calls": calls_by_era[era],
                "healthy": healthy_by_era[era],
            }
            for era in ("v0.2", "v0.3")
            if calls_by_era[era]
        },
        "by_harness": {
            h: {
                "recover_calls": calls_by_harness[h],
                "healthy": healthy_by_harness[h],
                "wipe": wipe_by_harness[h],
            }
            for h in sorted(calls_by_harness)
        },
        "steps_between_marker_and_call_v0.2": dict(
            sorted(
                (str(k), v)
                for k, v in dist_steps_since_marker.items()
                if any(c["era"] == "v0.2" for c in wipe_calls)
            )
        ),
        "calls": calls,
    }
    report["totals"]["sessions_skipped_outside_v0.2_window"] = sessions_skipped_window
    report["totals"]["time_budget_exceeded"] = budget_exhausted

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    t = report["totals"]
    print(f"sessions scanned:          {t['sessions_scanned']}")
    print(f"  skipped (pre-v0.2):      {t.get('sessions_skipped_outside_v0.2_window', 0)}")
    print(f"  time budget exceeded:    {t.get('time_budget_exceeded', False)}")
    print(f"sessions w/ recover calls: {t['sessions_with_recover_calls']}")
    print(f"sessions w/ wipe markers:  {t['sessions_with_wipe_markers']}")
    print(f"recover turns:             {t['recover_turns_total']} "
          f"(+{t['recover_turns_deduped_extra_calls']} retried calls deduped)")
    print(f"  on WIPE turns:           {t['wipe_turn_recover_calls']}")
    print(f"  on HEALTHY turns:        {t['healthy_turn_recover_calls']}")
    for era, d in report["by_era"].items():
        print(f"  era {era}: {d['recover_calls']} calls, {d['healthy']} healthy")
    for h, d in report["by_harness"].items():
        print(f"  {h}: {d['recover_calls']} calls ({d['healthy']} healthy, {d['wipe']} wipe)")
    print(f"report -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
