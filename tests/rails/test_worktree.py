"""Tests for rails.worktree: git worktree lifecycle for agent task runs.

Fixture: a SCRATCH git repo under tmp_path (`git init`, one commit on
`main`) -- never the real nextlane repo. Most tests use provision=False
(scratch repos have no web/ directory); the provisioning behaviour itself
(symlink web/node_modules, warn+continue when missing) is exercised
separately against scratch repos that DO have a web/node_modules dir.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from rails.worktree import Worktree, cleanup, create, worktree_for


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _branches(repo: Path, pattern: str = "rails/*") -> list[str]:
    """rails/* branch refs currently in `repo` (empty list if none)."""
    out = _git(repo, "branch", "--list", "--format=%(refname:short)", pattern).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _init_repo(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "rails-test@example.com")
    _git(repo, "config", "user.name", "Rails Test")
    (repo / "README.md").write_text("scratch\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit")
    return repo


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "scratch-repo")


# --- create() ---------------------------------------------------------------


def test_create_makes_worktree_dir_and_branch_off_base(scratch_repo):
    wt = create("my-task", repo_root=scratch_repo, provision=False)

    assert wt.path.is_dir()
    assert wt.path == scratch_repo / ".worktrees" / wt.path.name
    assert wt.branch.startswith("rails/my-task-")

    branches = _git(scratch_repo, "branch", "--list", wt.branch).stdout
    assert wt.branch in branches


def test_create_worktree_is_a_real_git_worktree(scratch_repo):
    wt = create("real-wt", repo_root=scratch_repo, provision=False)

    result = _git(wt.path, "rev-parse", "--show-toplevel")
    assert Path(result.stdout.strip()).resolve() == wt.path.resolve()


def test_create_branches_off_the_given_base_ref(scratch_repo):
    _git(scratch_repo, "checkout", "-b", "develop")
    (scratch_repo / "extra.txt").write_text("on develop\n")
    _git(scratch_repo, "add", "extra.txt")
    _git(scratch_repo, "commit", "-m", "develop-only commit")
    _git(scratch_repo, "checkout", "main")

    wt = create("from-develop", repo_root=scratch_repo, base_ref="develop", provision=False)

    assert (wt.path / "extra.txt").is_file()


def test_two_creates_get_distinct_ids_and_paths(scratch_repo):
    wt1 = create("dup", repo_root=scratch_repo, provision=False)
    wt2 = create("dup", repo_root=scratch_repo, provision=False)

    assert wt1.path != wt2.path
    assert wt1.branch != wt2.branch
    assert wt1.path.is_dir()
    assert wt2.path.is_dir()


def test_worktree_is_frozen():
    wt = Worktree(path=Path("/tmp/x"), branch="rails/x-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        wt.branch = "other"


# --- provisioning ------------------------------------------------------------


def test_provision_symlinks_node_modules_when_present(tmp_path):
    repo_root = _init_repo(tmp_path / "main-repo")
    node_modules = repo_root / "web" / "node_modules"
    node_modules.mkdir(parents=True)
    (node_modules / "marker.txt").write_text("present\n")

    wt = create("web-task", repo_root=repo_root, provision=True)

    link = wt.path / "web" / "node_modules"
    assert link.is_symlink()
    assert (link / "marker.txt").read_text() == "present\n"


def test_provision_warns_and_continues_when_node_modules_missing(tmp_path, caplog):
    repo_root = _init_repo(tmp_path / "main-repo-no-web")

    with caplog.at_level("WARNING"):
        wt = create("no-web", repo_root=repo_root, provision=True)

    assert wt.path.is_dir()  # worktree creation itself must still succeed
    assert not (wt.path / "web").exists()
    assert "node_modules" in caplog.text.lower()


def test_provision_false_skips_symlink_even_when_node_modules_present(tmp_path):
    repo_root = _init_repo(tmp_path / "main-repo-skip")
    node_modules = repo_root / "web" / "node_modules"
    node_modules.mkdir(parents=True)

    wt = create("skip-provision", repo_root=repo_root, provision=False)

    assert not (wt.path / "web" / "node_modules").exists()


# --- cleanup() ---------------------------------------------------------------


def test_cleanup_removes_worktree(scratch_repo):
    wt = create("to-remove", repo_root=scratch_repo, provision=False)
    assert wt.path.is_dir()

    cleanup(wt, repo_root=scratch_repo)

    assert not wt.path.exists()


def test_cleanup_tolerates_already_removed(scratch_repo):
    wt = create("gone", repo_root=scratch_repo, provision=False)
    cleanup(wt, repo_root=scratch_repo)
    assert not wt.path.exists()

    cleanup(wt, repo_root=scratch_repo)  # must not raise


def test_cleanup_force_removes_worktree_with_uncommitted_changes(scratch_repo):
    wt = create("dirty", repo_root=scratch_repo, provision=False)
    (wt.path / "uncommitted.txt").write_text("not committed\n")

    cleanup(wt, repo_root=scratch_repo, force=True)

    assert not wt.path.exists()


def test_cleanup_default_leaves_branch(scratch_repo):
    """`git worktree remove` alone never deletes the branch ref; the default
    (delete_branch=False) preserves it -- Task 6 pushes the branch before it
    cleans up, so a successful run keeps its branch."""
    wt = create("keeps-branch", repo_root=scratch_repo, provision=False)

    cleanup(wt, repo_root=scratch_repo)

    assert not wt.path.exists()
    assert wt.branch in _branches(scratch_repo)


def test_cleanup_delete_branch_removes_worktree_and_branch(scratch_repo):
    wt = create("no-leak", repo_root=scratch_repo, provision=False)
    assert wt.branch in _branches(scratch_repo)

    cleanup(wt, repo_root=scratch_repo, delete_branch=True)

    assert not wt.path.exists()
    assert _branches(scratch_repo) == []  # no leaked rails/* branch ref


def test_cleanup_delete_branch_tolerates_already_gone_branch(scratch_repo):
    wt = create("double-delete", repo_root=scratch_repo, provision=False)
    cleanup(wt, repo_root=scratch_repo, delete_branch=True)
    assert _branches(scratch_repo) == []

    # Second call: worktree AND branch already gone -- must not raise.
    cleanup(wt, repo_root=scratch_repo, delete_branch=True)


# --- worktree_for() ----------------------------------------------------------


def test_worktree_for_preserves_worktree_and_branch_on_success(scratch_repo):
    with worktree_for("keep-me", repo_root=scratch_repo, provision=False) as wt:
        path = wt.path

    assert path.is_dir()
    assert wt.branch in _branches(scratch_repo)  # branch survives for Task 6's push

    cleanup(wt, repo_root=scratch_repo, delete_branch=True)  # tidy up after ourselves


def test_worktree_for_force_removes_worktree_and_branch_on_exception(scratch_repo):
    """A failed run must leave NO debris behind -- neither the worktree dir
    nor its branch ref (a leaked rails/* branch would accumulate across
    failed retries)."""
    captured: dict[str, object] = {}

    with pytest.raises(RuntimeError, match="boom"):
        with worktree_for("blow-up", repo_root=scratch_repo, provision=False) as wt:
            captured["path"] = wt.path
            captured["branch"] = wt.branch
            raise RuntimeError("boom")

    assert not captured["path"].exists()
    assert captured["branch"] not in _branches(scratch_repo)


def test_worktree_for_yields_a_real_worktree(scratch_repo):
    with worktree_for("checked", repo_root=scratch_repo, provision=False) as wt:
        result = _git(wt.path, "rev-parse", "--show-toplevel")
        assert Path(result.stdout.strip()).resolve() == wt.path.resolve()
        cleanup(wt, repo_root=scratch_repo, delete_branch=True)
