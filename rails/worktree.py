"""Git worktree lifecycle for agent-driven task runs.

Spec ref: Phase-2 Task 4. Each rails agent task runs in its own throwaway git
worktree + branch pair so concurrent (or retried) runs never collide on the
main checkout, and a session gone wrong never leaves the primary tree dirty.

Provisioning tradeoff (documented once, here): a freshly created worktree
has no `web/node_modules` -- it's gitignored, so `git worktree add` can't
populate it from the index -- and no Python venv either. We symlink
`web/node_modules` from the `repo_root` checkout instead of re-running
`npm ci` in every worktree: it's near-instant and shares disk with the main
checkout's install. The cost: an agent session that adds or upgrades a web
dependency inside the worktree is mutating the SAME node_modules the main
checkout uses (a symlink shares the store, it doesn't copy it) -- accepted
for Phase 2, since worktrees are short-lived and task bodies steer agents
toward code changes rather than dependency churn. Revisit (a real `npm ci`
per worktree) if that assumption ever bites. If the symlink target doesn't
exist we warn and continue rather than falling back to a slow `npm ci`:
worktree creation must stay fast and non-web-only tasks (most of them)
shouldn't pay an npm-install tax they don't need; the cost is that the
gate's web-* steps will then fail loudly in that worktree, which is
acceptable signal, not silent breakage. The Python side needs no equivalent
step at all: `uv run` auto-syncs a per-worktree `.venv` against the repo's
lockfile the first time it's invoked inside the new worktree.

Two forward pointers for later tasks: (1) the symlink assumes `repo_root`'s
already-installed node_modules matches its own committed lockfile at
`base_ref` -- if the main checkout's install is stale relative to base_ref,
the worktree inherits that staleness (fine in practice: base_ref is the same
tree the operator develops against). (2) Agent tasks that ADD or upgrade web
dependencies are OUT OF SCOPE for symlink provisioning -- a new dep isn't in
the shared store, and an `npm install` inside the worktree mutates the main
checkout's node_modules; Task 9 task selection should avoid web-dependency
churn, and Task 5/7 carry this constraint into the prompts/AGENTS.md.
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str


def _short_id() -> str:
    # 6 hex chars -- ample collision resistance for short-lived, low-volume
    # worktrees (not a security token, just a disambiguator).
    return secrets.token_hex(3)


def create(
    task_slug: str,
    *,
    repo_root: Path,
    base_ref: str = "main",
    provision: bool = True,
) -> Worktree:
    """Create a new git worktree + branch off `base_ref` for one task run.

    branch = `rails/<task_slug>-<shortid>`,
    path   = `repo_root/.worktrees/<task_slug>-<shortid>`.

    `provision=False` skips making the worktree gate-capable (see module
    docstring) -- unit tests against scratch repos (no web/) pass this.
    """
    dir_name = f"{task_slug}-{_short_id()}"
    branch = f"rails/{dir_name}"
    path = repo_root / ".worktrees" / dir_name

    subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", str(path), "-b", branch, base_ref],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    wt = Worktree(path=path, branch=branch)

    if provision:
        _provision(wt, repo_root=repo_root)

    return wt


def _provision(wt: Worktree, *, repo_root: Path) -> None:
    """Make the worktree able to run the gate. See module docstring for the
    symlink-vs-npm-ci tradeoff and the warn-and-continue rationale."""
    node_modules_src = repo_root / "web" / "node_modules"
    node_modules_dst = wt.path / "web" / "node_modules"

    if node_modules_src.is_dir():
        # The worktree checkout has `web/` only if something under it is
        # tracked by git; make sure the parent dir exists before symlinking
        # so provisioning doesn't depend on that being true.
        node_modules_dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(node_modules_src, node_modules_dst)
    else:
        logger.warning(
            "provision: %s does not exist -- skipping web/node_modules symlink for "
            "worktree %s; the gate's web-* steps will fail loudly in this worktree "
            "until web deps are installed there",
            node_modules_src,
            wt.path,
        )


def cleanup(
    wt: Worktree,
    *,
    repo_root: Path,
    force: bool = False,
    delete_branch: bool = False,
) -> None:
    """Remove a worktree. Tolerates an already-removed worktree (a repeat
    call, or the directory having vanished out of band) instead of raising.

    `git worktree remove` deletes the CHECKOUT but never the branch ref, so
    the default leaves `wt.branch` in place -- Task 6 pushes the branch
    before it cleans up, so a successful run keeps its branch. Pass
    `delete_branch=True` to also `git branch -D <wt.branch>` (tolerating an
    already-gone branch); a failed run uses this so it leaks neither the
    worktree dir nor a dangling rails/* branch ref.
    """
    argv = ["git", "-C", str(repo_root), "worktree", "remove", str(wt.path)]
    if force:
        argv.append("--force")

    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 and wt.path.exists():
        raise RuntimeError(f"git worktree remove failed for {wt.path}: {result.stderr.strip()}")

    if delete_branch:
        # -D (force delete): the branch may be unmerged; that's expected for
        # a failed run's throwaway branch. returncode ignored -- an
        # already-gone branch (double cleanup) must not raise.
        subprocess.run(
            ["git", "-C", str(repo_root), "branch", "-D", wt.branch],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


@contextmanager
def worktree_for(
    task_slug: str,
    *,
    repo_root: Path,
    base_ref: str = "main",
    provision: bool = True,
) -> Iterator[Worktree]:
    """Create a worktree for the duration of the block.

    Asymmetric cleanup, BY DESIGN: on an EXCEPTION inside the block, the
    worktree is force-removed AND its branch deleted -- a failed task run
    must leave no debris behind, and a leaked rails/* branch would otherwise
    accumulate across failed retries. On SUCCESS the worktree AND branch are
    PRESERVED, not cleaned up: Task 6's PR flow needs the branch (and its
    commits) to survive after this context manager exits, until it's pushed
    and a PR opened -- Task 6 then calls `cleanup(delete_branch=True)` itself
    once the branch is safely on the remote. Callers that want the worktree
    gone unconditionally must call `cleanup()` themselves.
    """
    wt = create(task_slug, repo_root=repo_root, base_ref=base_ref, provision=provision)
    try:
        yield wt
    except BaseException:
        cleanup(wt, repo_root=repo_root, force=True, delete_branch=True)
        raise
