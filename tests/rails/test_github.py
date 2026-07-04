"""Tests for rails.github: the push + `gh pr create` helper.

No real network / no real git or gh binary involved -- `runner` is injected
(default subprocess.run in production) so every test drives a fake that
records argv/kwargs and returns a canned result. This mirrors the
fake-subprocess pattern already used in tests/rails/test_gate.py and
test_worktree.py.

open_pr makes up to THREE runner calls, in order:
  0. git rev-list --count <base>..<branch>   (pre-flight commit guard)
  1. git push -u origin <branch>
  2. gh pr create ...
so the fakes below supply results in that order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rails.github import GitHubError, open_pr, pr_body
from rails.worktree import Worktree

_PR_URL = "https://github.com/reinaldoq/nextlane/pull/42"


@dataclass
class _FakeResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class _FakeRunner:
    """Records every call; returns canned results keyed by call order."""

    results: list[_FakeResult]
    calls: list[tuple[list[str], dict]] = field(default_factory=list)

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return self.results[len(self.calls) - 1]


def _happy_runner(pr_url: str = _PR_URL) -> _FakeRunner:
    """A runner where every step succeeds: 1 commit ahead, push ok, PR made."""
    return _FakeRunner(
        results=[
            _FakeResult(returncode=0, stdout="1\n"),  # rev-list: 1 commit ahead
            _FakeResult(returncode=0),  # push
            _FakeResult(returncode=0, stdout=pr_url + "\n"),  # gh pr create
        ]
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Worktree:
    return Worktree(
        path=tmp_path / ".worktrees" / "build-feature-abc123", branch="rails/build-feature-abc123"
    )


# --- open_pr: pre-flight commit guard ---------------------------------


def test_open_pr_checks_commit_count_before_pushing(tmp_path, worktree):
    runner = _happy_runner()

    open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)

    revlist_argv, _ = runner.calls[0]
    assert revlist_argv == [
        "git",
        "-C",
        str(worktree.path),
        "rev-list",
        "--count",
        f"main..{worktree.branch}",
    ]


def test_open_pr_raises_and_never_pushes_when_zero_commits(tmp_path, worktree):
    runner = _FakeRunner(results=[_FakeResult(returncode=0, stdout="0\n")])

    with pytest.raises(GitHubError, match="no commits"):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)

    # Only the rev-list ran -- no orphaned remote branch, no ugly gh error.
    assert len(runner.calls) == 1


# --- open_pr: push ----------------------------------------------------


def test_open_pr_pushes_the_worktree_branch(tmp_path, worktree):
    runner = _happy_runner()

    open_pr(
        worktree=worktree,
        title="feat(rails-run): add vehicle stats endpoint",
        body="a body",
        repo_root=tmp_path,
        runner=runner,
    )

    push_argv, _push_kwargs = runner.calls[1]
    assert push_argv == [
        "git",
        "-C",
        str(worktree.path),
        "push",
        "-u",
        "origin",
        worktree.branch,
    ]


# --- open_pr: gh pr create --------------------------------------------


def test_open_pr_creates_pr_with_title_body_base_head(tmp_path, worktree):
    runner = _happy_runner()

    open_pr(
        worktree=worktree,
        title="feat(rails-run): add vehicle stats endpoint",
        body="a body\n\nfooter",
        repo_root=tmp_path,
        base="main",
        runner=runner,
    )

    pr_argv, _pr_kwargs = runner.calls[2]
    assert pr_argv == [
        "gh",
        "pr",
        "create",
        "--title",
        "feat(rails-run): add vehicle stats endpoint",
        "--body",
        "a body\n\nfooter",
        "--base",
        "main",
        "--head",
        worktree.branch,
    ]


def test_open_pr_returns_the_parsed_url(tmp_path, worktree):
    runner = _happy_runner()

    url = open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)

    assert url == _PR_URL


def test_open_pr_uses_custom_base(tmp_path, worktree):
    runner = _happy_runner("https://github.com/reinaldoq/nextlane/pull/7")

    open_pr(
        worktree=worktree,
        title="t",
        body="b",
        repo_root=tmp_path,
        base="develop",
        runner=runner,
    )

    # base flows into BOTH the pre-flight rev-list range and gh --base
    revlist_argv, _ = runner.calls[0]
    assert revlist_argv[-1] == f"develop..{worktree.branch}"
    pr_argv, _ = runner.calls[2]
    assert "--base" in pr_argv
    assert pr_argv[pr_argv.index("--base") + 1] == "develop"


# --- open_pr: error handling ------------------------------------------


def test_open_pr_raises_github_error_when_push_fails(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0, stdout="1\n"),  # rev-list ok
            _FakeResult(returncode=1, stderr="fatal: remote origin already exists"),  # push fails
        ]
    )

    with pytest.raises(GitHubError, match="push"):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)

    # gh pr create must never run if the push failed.
    assert len(runner.calls) == 2


def test_open_pr_raises_github_error_when_gh_pr_create_fails(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0, stdout="1\n"),
            _FakeResult(returncode=0),
            _FakeResult(returncode=1, stderr="pull request create failed: no commits"),
        ]
    )

    with pytest.raises(GitHubError, match="gh pr create"):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)


def test_open_pr_raises_github_error_on_empty_stdout(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0, stdout="1\n"),
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout=""),
        ]
    )

    with pytest.raises(GitHubError):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)


# --- pr_body -----------------------------------------------------------


def test_pr_body_neutral_footer_when_no_engine():
    body = pr_body("Adds the vehicle stats endpoint.")

    assert "Adds the vehicle stats endpoint." in body
    assert "🤖 Generated by Nextlane Rails" in body
    # engine-neutral: no vendor named, no "Claude Code" misattribution
    assert "Claude Code" not in body
    assert body.rstrip().endswith("🤖 Generated by Nextlane Rails")


def test_pr_body_names_engine_when_provided():
    body = pr_body("Summary", engine_label="Codex")

    assert "🤖 Generated by Codex via Nextlane Rails" in body
    assert body.rstrip().endswith("🤖 Generated by Codex via Nextlane Rails")


def test_pr_body_includes_optional_journal_note():
    body = pr_body(
        "Summary text",
        engine_label="Claude",
        journal_note="Journal: rails/journal/runs.jsonl#run-17",
    )

    assert "Journal: rails/journal/runs.jsonl#run-17" in body
    # footer must still be last
    assert body.rstrip().endswith("🤖 Generated by Claude via Nextlane Rails")


def test_pr_body_without_journal_note_omits_it():
    body = pr_body("Summary text")

    assert "Journal:" not in body
