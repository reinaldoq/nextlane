"""Tests for rails.github: the push + `gh pr create` helper.

No real network / no real git or gh binary involved -- `runner` is injected
(default subprocess.run in production) so every test drives a fake that
records argv/kwargs and returns a canned result. This mirrors the
fake-subprocess pattern already used in tests/rails/test_gate.py and
test_worktree.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rails.github import GitHubError, open_pr, pr_body
from rails.worktree import Worktree


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


@pytest.fixture
def worktree(tmp_path: Path) -> Worktree:
    return Worktree(
        path=tmp_path / ".worktrees" / "build-feature-abc123", branch="rails/build-feature-abc123"
    )


# --- open_pr ----------------------------------------------------------


def test_open_pr_pushes_the_worktree_branch(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout="https://github.com/reinaldoq/nextlane/pull/42\n"),
        ]
    )

    open_pr(
        worktree=worktree,
        title="feat(rails-run): add vehicle stats endpoint",
        body="a body",
        repo_root=tmp_path,
        runner=runner,
    )

    push_argv, _push_kwargs = runner.calls[0]
    assert push_argv == [
        "git",
        "-C",
        str(worktree.path),
        "push",
        "-u",
        "origin",
        worktree.branch,
    ]


def test_open_pr_creates_pr_with_title_body_base_head(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout="https://github.com/reinaldoq/nextlane/pull/42\n"),
        ]
    )

    open_pr(
        worktree=worktree,
        title="feat(rails-run): add vehicle stats endpoint",
        body="a body\n\nfooter",
        repo_root=tmp_path,
        base="main",
        runner=runner,
    )

    pr_argv, _pr_kwargs = runner.calls[1]
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
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout="https://github.com/reinaldoq/nextlane/pull/42\n"),
        ]
    )

    url = open_pr(
        worktree=worktree,
        title="t",
        body="b",
        repo_root=tmp_path,
        runner=runner,
    )

    assert url == "https://github.com/reinaldoq/nextlane/pull/42"


def test_open_pr_raises_github_error_when_push_fails(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=1, stderr="fatal: remote origin already exists"),
        ]
    )

    with pytest.raises(GitHubError, match="push"):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)

    # gh pr create must never run if the push failed.
    assert len(runner.calls) == 1


def test_open_pr_raises_github_error_when_gh_pr_create_fails(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=1, stderr="pull request create failed: no commits"),
        ]
    )

    with pytest.raises(GitHubError, match="gh pr create"):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)


def test_open_pr_raises_github_error_on_empty_stdout(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout=""),
        ]
    )

    with pytest.raises(GitHubError):
        open_pr(worktree=worktree, title="t", body="b", repo_root=tmp_path, runner=runner)


def test_open_pr_uses_custom_base(tmp_path, worktree):
    runner = _FakeRunner(
        results=[
            _FakeResult(returncode=0),
            _FakeResult(returncode=0, stdout="https://github.com/reinaldoq/nextlane/pull/7\n"),
        ]
    )

    open_pr(
        worktree=worktree,
        title="t",
        body="b",
        repo_root=tmp_path,
        base="develop",
        runner=runner,
    )

    pr_argv, _ = runner.calls[1]
    assert "--base" in pr_argv
    assert pr_argv[pr_argv.index("--base") + 1] == "develop"


# --- pr_body -----------------------------------------------------------


def test_pr_body_includes_footer():
    body = pr_body("Adds the vehicle stats endpoint.")

    assert "Adds the vehicle stats endpoint." in body
    assert "🤖 Generated with [Claude Code](https://claude.com/claude-code)" in body


def test_pr_body_includes_optional_journal_note():
    body = pr_body("Summary text", journal_note="Journal: rails/journal/runs.jsonl#run-17")

    assert "Journal: rails/journal/runs.jsonl#run-17" in body
    assert "🤖 Generated with [Claude Code](https://claude.com/claude-code)" in body
    # footer must still be last
    assert body.rstrip().endswith("(https://claude.com/claude-code)")


def test_pr_body_without_journal_note_omits_it():
    body = pr_body("Summary text")

    assert "Journal:" not in body
