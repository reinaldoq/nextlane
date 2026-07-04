"""The standalone review agent: an independent, read-only, cross-vendor
review against an EXISTING PR or an arbitrary git diff range.

Spec ref: Phase-2 Task 8. Unlike triage/migrate, this does NOT drive
`rails.agents.loop.run_agent_task` -- there's no worktree, no gate, no
retries, and no PR to open. It runs exactly ONE read-only reviewer session
directly against `cfg.repo_root` (never mutates anything -- see the
`readonly=True` adapter construction below) and hands back the verdict, so
it can double as a CI-style gate (`rails review --pr N`, exit 0/1) as well
as a manual "review this PR again" tool.

Reuses `rails.agents.loop.CHECKLIST` / `parse_verdict` and `rails.prompts.
compose_review` -- the SAME checklist and untrusted-data wrapping the
in-loop cross-vendor review step uses, so a diff can never trick this
reviewer any more than it can trick the loop's (see `compose_review`'s
docstring: the diff is always wrapped, and `parse_verdict` only ever reads
the reviewer's own final_message, never the diff).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from rich.console import Console

from rails.adapters import get_adapter
from rails.agents.loop import CHECKLIST, parse_verdict
from rails.config import RailsConfig
from rails.prompts import compose_review

console = Console()


class ReviewError(Exception):
    """Raised when fetching the diff or posting a PR comment fails."""


class _Runner(Protocol):
    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess: ...


def get_diff(
    *,
    pr: str | None = None,
    diff_range: str | None = None,
    repo_root: Path,
    runner: _Runner = subprocess.run,
) -> str:
    """The default `get_diff_fn`: `gh pr diff <pr>` when `pr` is given,
    else `git diff <diff_range>`. Exactly one of `pr` / `diff_range` must be
    given. Raises `ReviewError` (with the captured stderr) on a nonzero
    exit."""
    if pr is not None:
        argv = ["gh", "pr", "diff", str(pr)]
    elif diff_range is not None:
        argv = ["git", "diff", diff_range]
    else:
        raise ValueError("get_diff requires either pr or diff_range")

    result = runner(
        argv,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "(no stderr)"
        raise ReviewError(f"{' '.join(argv)} failed: {stderr}")
    return result.stdout


def gh_comment(pr: str, body: str, *, repo_root: Path, runner: _Runner = subprocess.run) -> None:
    """The default `gh_comment_fn`: `gh pr comment <pr> --body <body>`.
    Raises `ReviewError` (with the captured stderr) on a nonzero exit."""
    argv = ["gh", "pr", "comment", str(pr), "--body", body]
    result = runner(
        argv,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "(no stderr)"
        raise ReviewError(f"gh pr comment failed: {stderr}")


def review(
    cfg: RailsConfig,
    *,
    pr: str | None = None,
    diff_range: str | None = None,
    engine: str | None = None,
    comment: bool = False,
    get_diff_fn=get_diff,
    adapter_fn=get_adapter,
    gh_comment_fn=gh_comment,
) -> str:
    """Run a standalone, read-only cross-vendor review and return the
    verdict ("APPROVE" or "REQUEST_CHANGES").

    Exactly one of `pr` / `diff_range` must be given (raises `ValueError`
    otherwise). The diff is fetched via `get_diff_fn`, wrapped as untrusted
    data by `compose_review` (reused from `rails.agents.loop`, alongside
    `CHECKLIST`), and reviewed by a `readonly=True` adapter session run
    directly against `cfg.repo_root` -- this never touches a worktree
    because a read-only session can't mutate anything to review in the
    first place. `parse_verdict` (also reused from the loop) reads ONLY the
    reviewer's own final_message, so nothing embedded in the diff can forge
    a verdict. If `comment` and `pr` are both given, the verdict + reasoning
    is posted back to the PR via `gh_comment_fn`.
    """
    if pr is None and diff_range is None:
        raise ValueError("review requires either pr or diff_range")

    diff = get_diff_fn(pr=pr, diff_range=diff_range, repo_root=cfg.repo_root)
    prompt = compose_review(diff, checklist=CHECKLIST)

    reviewer = adapter_fn(engine or cfg.engine, cfg, readonly=True)
    session_result = reviewer.run(prompt, cwd=cfg.repo_root)
    verdict = parse_verdict(session_result.final_message)

    console.print(f"[bold]VERDICT: {verdict}[/bold]")
    console.print(session_result.final_message)

    if comment and pr is not None:
        gh_comment_fn(pr, session_result.final_message, repo_root=cfg.repo_root)

    return verdict
