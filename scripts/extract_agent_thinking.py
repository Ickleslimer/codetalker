"""Extract agent thinking about CodeTalker limitations from audit JSONL."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from codetalker.server import codetalk_read

HITS_PATH = Path(".audit/codetalker-usage-hits.jsonl")
OUTPUT_PATH = Path(".audit/codetalker-agent-thinking-only.md")
CATALOG_PATH = Path(".audit/codetalker-feedback-catalog.md")

BUILD_SESSION = "c8c466e3-d54c-4d90-92f6-acca7aa156cc"

LIMITATION_RE = re.compile(
    r"doesn't|does not|not expose|not readable|not currently readable|"
    r"empty results?|broken|limitation|thin cursor|thin sqlite|"
    r"failed|no access|can't|cannot|workaround|unavailable|"
    r"don't have any|doesn't surface|may need a root|wrong project|"
    r"not installed|no codetalker|direct transcript reads failed|"
    r"useful indexed search",
    re.IGNORECASE,
)

FALSE_POSITIVE_RE = re.compile(
    r"not a problem|no problem at all|not an? issue|nothing to fix",
    re.IGNORECASE,
)

# Skip thinking that's clearly about building CodeTalker, not using it
BUILD_NOISE_RE = re.compile(
    r"scaffold|pyproject|adapter_base|implementing the|write_to_file|"
    r"constructing the chatgpt adapter|building the mcp server|"
    r"pytest suite|registry\.py|schema_design",
    re.IGNORECASE,
)


def blob_from_hit(hit: dict) -> str:
    parts = [
        hit.get("preview", ""),
        hit.get("tool_result_preview", ""),
        " ".join(hit.get("context_before") or []),
        " ".join(hit.get("context_after") or []),
    ]
    return " ".join(parts)


def step_text(step: dict) -> str:
    chunks: list[str] = []
    role = step.get("actor", {}).get("role", "?")
    for block in step.get("blocks", []):
        for key in ("text", "content", "tool_name", "detail"):
            val = block.get(key)
            if val:
                chunks.append(str(val))
    return f"[{role}] " + " ".join(chunks).replace("\n", " ").strip()


def load_filtered_hits() -> list[dict]:
    hits: list[dict] = []
    for line in HITS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        hit = json.loads(line)
        if hit.get("match_type") != "content":
            continue
        sid = hit.get("session_id", "")
        blob = blob_from_hit(hit)
        if sid == BUILD_SESSION:
            if not LIMITATION_RE.search(blob):
                continue
            if BUILD_NOISE_RE.search(blob) and not LIMITATION_RE.search(
                hit.get("preview", "")
            ):
                continue
        if not LIMITATION_RE.search(blob):
            continue
        if FALSE_POSITIVE_RE.search(blob):
            continue
        hits.append(hit)
    return hits


def dedupe_hits(hits: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, int | None, str]] = set()
    unique: list[dict] = []
    for hit in hits:
        preview_key = (hit.get("preview") or "")[:80].lower()
        key = (
            hit.get("harness", ""),
            hit.get("session_id", ""),
            hit.get("step_index"),
            preview_key,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def fetch_context(harness: str, session_id: str, step_index: int | None) -> list[str]:
    if step_index is None:
        return []
    try:
        offset = max(0, step_index - 3)
        data = json.loads(
            codetalk_read(
                session_id=session_id,
                harness=harness,
                conversation_only=False,
                include_thinking=False,
                offset=offset,
                from_end=False,
                limit=7,
            )
        )
        return [step_text(s) for s in data.get("steps", []) if step_text(s).strip()]
    except Exception as e:
        return [f"(deep-read failed: {e})"]


def main() -> None:
    hits = dedupe_hits(load_filtered_hits())
    hits.sort(key=lambda h: (h.get("harness", ""), h.get("session_id", ""), h.get("step_index") or 0))

    by_session: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for hit in hits:
        by_session[(hit["harness"], hit["session_id"])].append(hit)

    lines = [
        "# CodeTalker Agent Thinking (limitation/failure excerpts only)",
        "",
        f"Filtered from `{HITS_PATH.name}`: `match_type=content`, limitation language,",
        f"excluding build-session `{BUILD_SESSION[:8]}…` except explicit failure reports.",
        "",
        f"**Entries:** {len(hits)} hits across {len(by_session)} sessions",
        "",
        "---",
        "",
    ]

    entry_num = 0
    for (harness, session_id), session_hits in sorted(by_session.items()):
        display = session_hits[0].get("display_name") or session_id
        lines.append(f"## {harness} — {display}")
        lines.append("")
        lines.append(f"- **session_id:** `{session_id}`")
        lines.append(f"- **hits in this extract:** {len(session_hits)}")
        lines.append("")

        for hit in session_hits:
            entry_num += 1
            step_idx = hit.get("step_index")
            lines.append(f"### Entry {entry_num} (step {step_idx})")
            lines.append("")
            lines.append(f"- **matched_term:** `{hit.get('matched_term')}`")
            lines.append(f"- **preview:** {hit.get('preview', '').strip()}")
            lines.append("")

            if hit.get("context_before"):
                lines.append("**Audit context (before):**")
                for ctx in hit.get("context_before", [])[:2]:
                    lines.append(f"> {ctx[:400]}")
                lines.append("")

            if step_idx is not None:
                lines.append("**Transcript window (±3 steps):**")
                lines.append("")
                for row in fetch_context(harness, session_id, step_idx):
                    if len(row) > 500:
                        row = row[:497] + "..."
                    lines.append(f"- {row}")
                lines.append("")

            lines.append("---")
            lines.append("")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(hits)} entries to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
