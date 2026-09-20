"""codetalker_sync.py — one command from working tree to a synced device.

Publishes the local codetalker working tree (tests -> version bump ->
commit -> push branch FIRST, then tag -> poll PyPI) and refreshes every
local installation tier that is not already automatic:

  editable (Freebuff)  .pth into the source tree -> zero-touch already;
                       agent_guidance._package_version() prefers source
                       __version__, so staleness cannot make the server
                       lie — and the publish leg now REFRESHES the
                       .dist-info after the bump (uv pip install -e,
                       verified by read-back), so metadata never drifts
                       from source in the first place (drift seen twice
                       in the 0.3.2/0.3.3 audits).
  uvx (Cursor, Antigravity, Codex)  ephemeral envs are pinned in uv's
                       cache until uv re-resolves. We warm the shared
                       cache with the new wheel (uvx -U --refresh), delete
                       only env dirs whose pyvenv.cfg mentions the dist,
                       and prove the normal launch path now resolves the
                       new version by executing it.
  freebuff registry     ~/.agents/mcp.json (the launch registry Freebuff
                       actually reads — orchestrator-verified) is re-merged
                       via the installer's freebuff mode: the codetalker
                       entry is kept on the canonical published form,
                       other servers preserved, file created if missing.
                       Takes effect on the next Freebuff restart; the
                       consent sidecar stays UI-managed.

Local-only mode (--no-publish): dev sanity + Freebuff registry refresh
+ uvx cache prep + matrix report, with no commit, no push, no PyPI
dependency (the registry refresh itself is offline-safe). Note that in this
mode the uvx leg pulls the latest PUBLISHED release, not the working tree.

Requires: git, gh (authenticated), uv/uvx on PATH. The publish leg needs
the PyPI trusted publisher (owner Ickleslimer, repo codetalker, workflow
publish.yml, environment pypi).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_INIT = REPO / "src" / "codetalker" / "__init__.py"
DIST = "codetalker-mcp"
PYPI_JSON = f"https://pypi.org/pypi/{DIST}/json"
VERSION_RE = re.compile(r"^__version__\s*=\s*[\"'](.+?)[\"']", re.M)

GIT = ["git", "-c", "safe.directory=" + str(REPO).replace("\\", "/")]


def require_clean_tree() -> None:
    """Refuse to release unless the tree is clean before the bump.

    v0.3.5's run released a tag whose checkout did not contain the very
    feature being released (source edits were still uncommitted when the
    script ran; the script commits only its own version bump, and CI builds
    the wheel from the tag). The guard turns that silent failure class into
    a loud refusal.
    """
    st = subprocess.run(
        GIT + ["status", "--porcelain"], capture_output=True, text=True
    )
    if st.returncode != 0:
        die(f"git status failed: {st.stderr.strip()}")
    if st.stdout.strip():
        die(
            "working tree is dirty — commit everything first, then run the "
            "release.\n  The tag is built and CI publishes from the TAGGED "
            "COMMIT, not this working tree; an uncommitted feature would be "
            "silently missing from the wheel.\n  "
            + "\n  ".join(st.stdout.strip().splitlines())
        )
    print("  tree clean — tag will match the released code")

# The sync script may run under any interpreter; make the repo's src tree
# importable so the installer is always reachable (it is stdlib-only).
sys.path.insert(0, str(REPO / "src"))

from codetalker.install_harnesses import install as install_harness_configs  # noqa: E402
from codetalker.install_harnesses import config_targets  # noqa: E402


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def die(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def step(msg: str) -> None:
    print(f"[sync] {msg}")


def venv_python() -> Path:
    exe = REPO / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not exe.is_file():
        die(f"repo venv python not found at {exe}")
    return exe


def pypi_version(timeout: float = 15.0) -> str | None:
    """Latest version PyPI serves; None on any network/parse failure."""
    try:
        req = urllib.request.Request(PYPI_JSON, headers={"User-Agent": "codetalker-sync/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(json.load(resp)["info"]["version"])
    except Exception:
        return None


def uv_cache_dir() -> Path | None:
    try:
        out = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def uv_tool_dir() -> Path | None:
    try:
        out = subprocess.run(["uv", "tool", "dir"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def find_pinned_uvx_envs() -> list[Path]:
    """Ephemeral uvx env dirs whose pyvenv.cfg mentions the dist.

    uv's layout varies by version; we scan the plausible environment dirs
    and only ever flag dirs that self-identify via pyvenv.cfg content, so
    a layout change degrades to finding nothing (never to deleting wrong
    directories).
    """
    cache = uv_cache_dir()
    if cache is None:
        return []
    found: list[Path] = []
    for sub in ("environments-v2", "environments-v1", "builds-v0", "archive-v0"):
        base = cache / sub
        if not base.is_dir():
            continue
        for env in base.iterdir():
            cfg = env / "pyvenv.cfg"
            if not env.is_dir() or not cfg.is_file():
                continue
            try:
                if DIST in cfg.read_text(encoding="utf-8", errors="replace"):
                    found.append(env)
            except OSError:
                continue
    return found


def find_stale_uvx_envs() -> list[Path]:
    """Pinned cache envs + ORPHANED receipt-less tool envs for the dist.

    Two stale sources found live during the first publish-leg run:
    (1) cache environments pinned to the old wheel (environments-v2),
    (2) a persisted tool environment under `uv tool dir` with NO
    uv-receipt.toml — an orphan uvx reuses while ignoring newer versions
    and refusing -U ("Tools cannot be upgraded via uvx"). Managed tool
    installs carry a receipt and are respected; orphans are removed.
    """
    found = find_pinned_uvx_envs()
    tools = uv_tool_dir()
    if tools and tools.is_dir():
        for env in tools.iterdir():
            if env.is_dir() and env.name == DIST and not (env / "uv-receipt.toml").exists():
                found.append(env)
    return found


def probe_uvx_resolution(refresh: bool = False, timeout: int = 300) -> str | None:
    """Version a normal `uvx --from <dist> python -c ...` launch resolves.

    Returns the reported version, or None on timeout/failure. With
    refresh=True the resolution is forced to revalidate (used as the
    unconditional gate: it must see the new wheel even while a plain-path
    launch may legally still serve its cached index page).
    """
    cmd = ["uvx"]
    if refresh:
        cmd.append("--refresh")
    cmd += ["--from", DIST, "python", "-c", "import codetalker; print(codetalker.__version__)"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[-1].strip() if out and r.returncode == 0 else None


def plain_path_eventually(new_version: str, attempts: int = 6, wait: int = 120) -> bool:
    """Wait for the PLAIN launch path to serve the new version.

    Plain uvx re-resolves only when its cached PyPI index page goes stale
    (uv respects the index max-age, ~600s on PyPI). If a harness has a
    codetalker server live from the cache, `uv cache clean` may block on
    the shared lock — so waiting is the correct primary strategy and the
    clean is best-effort only.
    """
    for i in range(attempts):
        served = probe_uvx_resolution()
        if served == new_version:
            print(f"  OK — plain `uvx --from {DIST} codetalker` launches {served}")
            return True
        if i < attempts - 1:
            print(f"  plain path serves {served!r} (index page still fresh); "
                  f"retrying in {wait}s")
            time.sleep(wait)
    return False


def refresh_uvx(new_version: str, old_version: str) -> None:
    """Warm the shared cache with the new wheel, drop pinned envs, prove it."""
    step("uvx tier: warm shared cache with the new wheel")
    run(["uvx", "-U", "--refresh", "--from", DIST, "codetalker", "--help"], timeout=300)

    step("uvx tier: remove envs pinned to the old wheel (cache + orphaned tool envs)")
    for env in find_stale_uvx_envs():
        print(f"  removing {env}")
        shutil.rmtree(env, ignore_errors=True)

    # uv caches PyPI's simple-index page with its own max-age; drop it so
    # the plain path re-resolves NOW instead of at TTL expiry. Best-effort:
    # with a harness server live from this cache, the exclusive clean blocks
    # on the shared lock — that is normal, and plain_path_eventually's
    # bounded retries are the correct path to green in that case.
    step("uvx tier: drop the dist's cached index pages (best-effort)")
    try:
        subprocess.run(["uv", "cache", "clean", DIST],
                       capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        print("  clean skipped: cache lock held (a live server is using it) — "
              "plain-path retries will cover this")

    step(f"uvx tier: prove the forced-resolution gate resolves {new_version}")
    forced = probe_uvx_resolution(refresh=True)
    if forced != new_version:
        die(f"forced refresh still resolves {forced!r} (wanted {new_version!r}) — "
            f"real distribution problem; investigate before retrying")
    print(f"  forced-resolution gate: {forced}")

    step(f"uvx tier: wait for the plain launch path to resolve {new_version}")
    if not plain_path_eventually(new_version):
        die(f"plain uvx path still serves the old version after "
            f"index-TTL retries — unexpected: forced resolution sees "
            f"{new_version}. Check uv cache state manually.")


def refresh_freebuff_registry(dry: bool) -> None:
    """Re-apply the installer's freebuff mode so ~/.agents/mcp.json — the
    launch registry Freebuff actually reads — keeps its codetalker entry on
    the canonical published form (created if missing; other servers such as
    desktop-control preserved). Takes effect on the next Freebuff restart;
    the consent sidecar stays UI-managed."""
    step("freebuff tier: refresh registry entry (installer --mode freebuff)")
    for line in install_harness_configs(
        project_root=REPO, mode="freebuff", harnesses=["Freebuff"], dry_run=dry
    ):
        print("  " + line)


def install_matrix() -> None:
    """Report every local install tier against the source version."""
    from codetalker import __version__

    step(f"install matrix (source __version__ = {__version__})")
    for name, path in config_targets().items():
        if not path.is_file():
            print(f"  {name:22s} no config ({path}) — skipped")
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as e:
            print(f"  {name:22s} unreadable ({e})")
            continue
        if name == "Codex":
            wired = DIST in text
        else:
            try:
                wired = DIST in json.dumps(json.loads(text))
            except ValueError:
                wired = False
        print(f"  {name:22s} {'uvx -> ' + DIST if wired else 'NOT on the published package'}  ({path})")
    print(f"  {'Freebuff (dev-edit)':22s} editable, zero-touch; restart re-reads source  ({REPO})")


def bump_patch_version() -> tuple[str, str]:
    text = SRC_INIT.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        die(f"cannot find __version__ in {SRC_INIT}")
    old = m.group(1)
    parts = old.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        die(f"__version__ {old!r} is not X.Y.Z; bump manually")
    new = f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return old, new


def publish_leg(dry: bool) -> None:
    old, new = bump_patch_version()
    print(f"[sync] release {old} -> {new}")

    if not dry:
        require_clean_tree()
    if dry:
        print("  [dry] would rewrite __init__.py, commit, push branch, tag, push tag,")
        print("  [dry] poll PyPI, refresh editable metadata, uvx cache/envs, Freebuff registry,")
        print("  [dry] verify resolution")
        refresh_freebuff_registry(dry)
        return

    step("bumping src/codetalker/__init__.py")
    SRC_INIT.write_text(
        VERSION_RE.sub(lambda _: f'__version__ = "{new}"', SRC_INIT.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    step("refreshing dev editable install metadata (uv pip install -e)")
    run(["uv", "pip", "install", "-e", str(REPO), "--python", str(venv_python()), "-q"])
    got = subprocess.run(
        [str(venv_python()), "-c",
         f"import importlib.metadata as m; print(m.version('{DIST}'))"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if got != new:
        die(f"editable metadata still reports {got!r} (wanted {new!r})")
    print(f"  editable metadata now {got}")

    step("commit + push branch FIRST, then tag separately (2026-09-19 lesson)")
    run(GIT + ["add", "src/codetalker/__init__.py"], cwd=REPO)
    run(GIT + ["commit", "-m", f"chore: release v{new}"], cwd=REPO)
    run(GIT + ["push", "origin", "main"], cwd=REPO)
    run(GIT + ["tag", f"v{new}"], cwd=REPO)
    run(GIT + ["push", "origin", f"v{new}"], cwd=REPO)

    step(f"polling PyPI for {DIST} {new} (publish workflow: build -> smoke gate -> upload)")
    deadline = time.time() + 150
    while True:
        live = pypi_version()
        if live == new:
            print(f"  PyPI is serving {live}")
            break
        if time.time() > deadline:
            die(f"PyPI never served {new} within 150s — check the publish.yml run "
                f"(gh run list --workflow publish.yml)")
        print(f"  registry says {live!r}; retrying in 10s")
        time.sleep(10)

    refresh_uvx(new, old)
    refresh_freebuff_registry(dry)


def local_leg(dry: bool) -> None:
    step("dev tier: editable install already maps to the source tree (zero-touch)")
    print(f"  source of truth: {SRC_INIT}")

    # Offline-safe, so it runs before any network-dependent step (and before
    # the dry-run return — the installer prints its own [dry-run] lines).
    refresh_freebuff_registry(dry)

    if dry:
        print("  [dry] would run: uvx -U --refresh --from", DIST, "codetalker --help")
        print("  [dry] would remove pinned uvx envs, then verify resolution")
        return

    old = re.search(VERSION_RE, SRC_INIT.read_text(encoding="utf-8")).group(1)
    published = pypi_version()
    step(f"uvx tier: prep (published={published!r}, source={old!r})")
    if published is None:
        print("  PyPI unreachable — skipping uvx refresh (offline-safe)")
        return
    if published == old:
        print(f"  PyPI already serves {published}; warming cache only")
        run(["uvx", "-U", "--refresh", "--from", DIST, "codetalker", "--help"], timeout=300)
        served = probe_uvx_resolution(refresh=True)
        print(f"  forced-resolution probe: {served!r}")
        print(f"  PyPI serves {published} but source is {old}: the working tree is AHEAD. "
              f"Run without --no-publish to release it; refreshing cache to {published} anyway")
        run(["uvx", "-U", "--refresh", "--from", DIST, "codetalker", "--help"], timeout=300)
    for env in find_stale_uvx_envs():
        print(f"  removing {env}")
        shutil.rmtree(env, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync codetalker working tree to every local install tier (and optionally PyPI).")
    ap.add_argument("--dry-run", action="store_true", help="print mutating steps without executing them")
    ap.add_argument("--no-publish", action="store_true", help="local tiers only; no commit, push, or release")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the test suite first (not recommended)")
    args = ap.parse_args(argv)

    if args.dry_run:
        print("[sync] DRY-RUN: no file, git, cache, or environment changes")

    if not args.skip_tests:
        step("running test suite")
        run([venv_python(), "-m", "pytest", "-q"], cwd=REPO)

    if args.no_publish:
        local_leg(args.dry_run)
    else:
        publish_leg(args.dry_run)

    install_matrix()
    print("[sync] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
