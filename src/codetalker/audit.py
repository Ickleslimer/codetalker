from __future__ import annotations

import json
import sys
from pathlib import Path

import click

import codetalker.adapters  # noqa: F401
from codetalker.registry import registry
from codetalker.search import SearchOptions, SearchProgress, search_sessions
from codetalker.server import FIXTURE_TESTED_HARNESSES

DEFAULT_AUDIT_QUERIES = [
    "codetalker",
    "codetalk_",
    "code talker",
    "mcp__codetalker",
    "user-codetalker",
]

DEFAULT_SINCE = "2026-08-22"


def _harnesses_with_local_data(root_path: str | None) -> list[str]:
    harnesses: list[str] = []
    for h in registry.list_canonical_harnesses():
        adapter = registry.get(h)
        if not adapter:
            continue
        try:
            sessions = adapter.discover_sessions(root_path=root_path)
        except Exception:
            continue
        if sessions:
            harnesses.append(h)
        elif h in FIXTURE_TESTED_HARNESSES:
            continue
    return harnesses


def run_audit(
    since: str,
    search_scope: str,
    queries: str,
    harness: str | None,
    root_path: str | None,
    output: Path,
    include_context_steps: int,
) -> dict:
    """Batch-search local transcripts for CodeTalker mentions and agent usage."""
    query_list = [q.strip() for q in queries.split(",") if q.strip()]
    if harness:
        harnesses = [registry.get_canonical_name(harness)]
    else:
        harnesses = _harnesses_with_local_data(root_path)

    if not harnesses:
        raise click.ClickException("No harnesses with local session data found.")

    output.parent.mkdir(parents=True, exist_ok=True)
    progress = SearchProgress()

    click.echo(
        f"Auditing {len(harnesses)} harness(es) since {since} "
        f"(scope={search_scope}, terms={query_list})",
        err=True,
    )

    options = SearchOptions(
        query=query_list[0] if query_list else "",
        queries=query_list,
        since=since,
        limit=0,
        max_sessions_to_search=0,
        max_hits_per_session=0,
        search_scope=search_scope.lower(),
        match_tool_names=True,
        include_context_steps=include_context_steps,
        root_path=root_path,
        harnesses=harnesses,
    )

    matches = search_sessions(options, progress=progress)

    with output.open("w", encoding="utf-8") as fh:
        for hit in matches:
            fh.write(json.dumps(hit, ensure_ascii=False) + "\n")

    summary = {
        "since": since,
        "search_scope": search_scope,
        "queries": query_list,
        "harnesses": harnesses,
        "harnesses_searched": progress.harnesses_searched,
        "sessions_searched": progress.sessions_searched,
        "sessions_skipped_since": progress.sessions_skipped_since,
        "match_count": len(matches),
        "output": str(output),
    }

    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    click.echo(
        f"Done: {len(matches)} hits from {progress.sessions_searched} sessions "
        f"-> {output}",
        err=True,
    )
    return summary


@click.command("audit")
@click.option(
    "--since",
    default=DEFAULT_SINCE,
    show_default=True,
    help="Only include sessions with last_activity on or after this ISO date.",
)
@click.option(
    "--search-scope",
    type=click.Choice(["tail", "full"], case_sensitive=False),
    default="full",
    show_default=True,
    help="Scan full transcripts or only recent tail steps.",
)
@click.option(
    "--queries",
    default=",".join(DEFAULT_AUDIT_QUERIES),
    show_default=True,
    help="Comma-separated OR search terms.",
)
@click.option(
    "--harness",
    default=None,
    help="Limit to one harness (default: all harnesses with local data).",
)
@click.option(
    "--root-path",
    default=None,
    help="Optional discovery root override.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path(".audit/codetalker-usage-hits.jsonl"),
    show_default=True,
    help="JSONL output path for search hits.",
)
@click.option(
    "--include-context-steps",
    default=2,
    show_default=True,
    help="Steps of context before/after each content hit.",
)
def audit_cmd(
    since: str,
    search_scope: str,
    queries: str,
    harness: str | None,
    root_path: str | None,
    output: Path,
    include_context_steps: int,
) -> None:
    """Batch-search local transcripts for CodeTalker mentions and agent usage."""
    summary = run_audit(
        since=since,
        search_scope=search_scope,
        queries=queries,
        harness=harness,
        root_path=root_path,
        output=output,
        include_context_steps=include_context_steps,
    )
    click.echo(json.dumps(summary, indent=2))


def main() -> None:
    """Entry point for codetalker-audit script."""
    audit_cmd()
