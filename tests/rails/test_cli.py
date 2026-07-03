"""Pure-unit tests for the rails CLI skeleton.

No Postgres, no JWKS server, no api/ fixtures -- these tests only exercise
rails.cli / rails.config in-process via typer.testing.CliRunner.
"""

import dataclasses
import os
import shutil

import pytest
from typer.testing import CliRunner

from rails.cli import app
from rails.config import RailsConfig

runner = CliRunner()

COMMANDS = ("build-feature", "triage", "migrate", "review", "gate", "engines")
ENGINES = ("claude", "codex", "gemini")


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


# --- CLI: stub commands exit 1 with "not implemented" --------------------


@pytest.mark.parametrize(
    "args",
    [
        ["build-feature", "add a widget"],
        ["triage", "last 24h"],
        ["migrate", "add index on vehicles.vin"],
        ["review", "--pr", "123"],
        ["gate"],
    ],
)
def test_stub_commands_exit_1_not_implemented(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not implemented" in result.output.lower()


# --- RailsConfig -----------------------------------------------------------


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


def test_config_is_frozen():
    cfg = RailsConfig.load()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.engine = "gemini"


def test_allowed_env_excludes_canary_and_includes_path(monkeypatch):
    monkeypatch.setenv("RAILS_TEST_CANARY", "leak-me-not")
    cfg = RailsConfig.load()

    env = cfg.allowed_env()

    assert "RAILS_TEST_CANARY" not in env
    assert "PATH" in env
    assert env["PATH"] == os.environ["PATH"]


def test_allowed_env_includes_git_star(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Rails Bot")
    cfg = RailsConfig.load()

    env = cfg.allowed_env()

    assert env.get("GIT_AUTHOR_NAME") == "Rails Bot"


def test_allowed_env_merges_explicit_extras(monkeypatch):
    monkeypatch.delenv("RAILS_TEST_EXTRA", raising=False)
    cfg = RailsConfig.load()

    env = cfg.allowed_env(extra={"RAILS_TEST_EXTRA": "explicit-value"})

    assert env["RAILS_TEST_EXTRA"] == "explicit-value"
