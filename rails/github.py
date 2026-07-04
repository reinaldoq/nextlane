"""Push a task-run's worktree branch and open a PR for it via `gh`.

Spec ref: Phase-2 Task 5. `open_pr` is the only network-touching seam in
Task 6's loop, so `runner` (default `subprocess.run`) is an injected
collaborator -- unit tests pass a fake that records argv and returns a
canned result, exercising zero real git/gh/network activity.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from rails.worktree import Worktree


class GitHubError(Exception):
    """Raised when the push or `gh pr create` step fails."""


class _Runner(Protocol):
    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess: ...


def pr_body(summary: str, *, journal_note: str | None = None) -> str:
    """Build a PR body: `summary`, an optional journal pointer, then the
    repo's standard Claude Code footer -- always last."""
    parts = [summary.rstrip()]
    if journal_note:
        parts.append(journal_note)
    parts.append("🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    return "\n\n".join(parts)


def open_pr(
    *,
    worktree: Worktree,
    title: str,
    body: str,
    repo_root: Path,
    base: str = "main",
    runner: _Runner = subprocess.run,
) -> str:
    """Push `worktree.branch` and open a PR for it against `base`.

    `body` is expected to already carry the standard footer -- build it
    with `pr_body()`. Returns the PR URL `gh pr create` prints to stdout.
    Raises `GitHubError` (with the captured stderr) if either the push or
    `gh pr create` exits non-zero, or if `gh pr create` prints no URL.

    `gh pr create` runs with `cwd=repo_root` (not the worktree) so it keeps
    working even in the narrow window right before Task 6 tears the
    worktree down -- `repo_root`'s `origin` remote is the same one the
    branch was just pushed to, so `gh`'s repo detection resolves identically
    either way.
    """
    push_result = runner(
        ["git", "-C", str(worktree.path), "push", "-u", "origin", worktree.branch],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if push_result.returncode != 0:
        stderr = (push_result.stderr or "").strip() or "(no stderr)"
        raise GitHubError(f"git push failed for branch {worktree.branch}: {stderr}")

    pr_result = runner(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            base,
            "--head",
            worktree.branch,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if pr_result.returncode != 0:
        stderr = (pr_result.stderr or "").strip() or "(no stderr)"
        raise GitHubError(f"gh pr create failed for branch {worktree.branch}: {stderr}")

    url = (pr_result.stdout or "").strip()
    if not url:
        raise GitHubError(
            f"gh pr create for branch {worktree.branch} produced no output; expected a PR URL"
        )
    # gh prints the URL as (typically) the only line of stdout on success;
    # take the last non-empty line defensively in case of leading output.
    return url.splitlines()[-1].strip()
