"""Shared subprocess-adapter machinery: the `AgentSession` protocol, the
`SessionResult` every adapter returns, and `_SubprocessAdapter`, the base
class real engine adapters (claude, codex, gemini) build on.

Spec §7 (Rails / agent security): every session subprocess's env is built
ONLY from `RailsConfig.allowed_env(extra_env)` -- never `os.environ`
wholesale. stdout is streamed line-by-line to a transcript file on disk
(under `<cwd>/.rails-transcripts/`, stderr to a `.stderr.log` sidecar) so a
run can be inspected after the fact even if a parser misses something. On
timeout the whole process group is killed (never just the direct child --
engine CLIs can spawn their own subprocesses) and a `SessionError` is
raised.

Subclass seam contract (Task 3 adapters override ONLY these, never run()):
  - `name` (class attr) and `emits_terminal_result` (class attr, see below)
  - `default_binary() -> list[str]`
  - `build_argv(prompt, *, cwd, out_file) -> list[str]` -- PURE: no side
    effects, no instance-state mutation; deterministic from its arguments
    plus construction-time config. `cwd` is the session working directory
    (codex needs `-C <cwd>`); `out_file` is a per-run scratch path under
    .rails-transcripts/ the engine MAY be told to write to (codex: `-o
    out_file` for the reliable final message); engines that don't need
    them ignore them (claude ignores both).
  - `_parse(lines, *, cwd, out_file) -> ParsedTranscript` -- lines is the
    raw stdout line list; cwd/out_file let a parser read files the engine
    wrote during the run (codex reads out_file after exit).

Known accepted limitations (fine for Phase 2's strictly sequential runs;
revisit before any parallel orchestration): transcript filenames are
timestamp-based and could collide if two same-engine runs started in the
same cwd within the same microsecond, and the full stdout line list is
buffered in memory for parsing (transcripts are small; agent sessions are
chat-scale, not data-scale).
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol

from rails.config import RailsConfig


@dataclass
class SessionResult:
    """Outcome of one agent session.

    Note for Task 6's loop: `ok and not explicit_result` is suspicious --
    the process exited 0 but the engine never emitted its terminal result
    event (truncated stream, wrapper swallowing output, ...). For engines
    whose adapter sets emits_terminal_result=False the field is always True
    so the heuristic never false-flags them.
    """

    engine: str
    ok: bool  # process exit 0 (and, where the engine reports it, no in-band error)
    final_message: str  # engine's last assistant text
    transcript_path: Path  # raw stdout event stream saved to disk (stderr: .stderr.log sidecar)
    duration_s: float
    cost_usd: float | None  # claude reports; others None
    raw_exit_code: int
    explicit_result: bool  # engine emitted its terminal result event (claude: `result`)


@dataclass
class ParsedTranscript:
    """What an engine-specific parser extracted from a session's output.

    A dataclass (not a tuple) so future engine fields (num_turns, model
    name, token counts, ...) can be added without touching every adapter.
    """

    final_message: str = ""
    cost_usd: float | None = None
    result_ok: bool = True  # engine's own in-band verdict; True = trust the exit code
    saw_result: bool = False  # engine emitted its terminal result event


class SessionError(RuntimeError):
    """Raised on timeout, spawn failure, or transcript-capture failure
    (NOT on nonzero engine exit -- that's ok=False)."""


class AgentSession(Protocol):
    name: str

    def run(
        self,
        prompt: str,
        *,
        cwd: Path,
        timeout_s: int = 1800,
        extra_env: dict[str, str] | None = None,
    ) -> SessionResult: ...


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group the child started (it was launched with
    start_new_session=True, so its pgid == its pid). Best-effort: the group
    may already be gone by the time we get here."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _pump_stream(stream: IO[str], write_line) -> None:
    """Drain a subprocess stream line-by-line into write_line. Module-level
    so tests can inject a failing pump and prove capture failures surface."""
    for raw_line in stream:
        write_line(raw_line.rstrip("\n"))


class _SubprocessAdapter:
    """Base class for engine adapters that drive a subprocess CLI.

    See the module docstring for the full subclass seam contract. In short:
    subclasses override name / emits_terminal_result / default_binary /
    build_argv / _parse and NEVER run().
    """

    name: str
    # False for engines with no terminal-result event at all (gemini):
    # SessionResult.explicit_result is then pinned True so downstream
    # "ok and not explicit_result" checks never false-flag them.
    emits_terminal_result: bool = True

    def __init__(self, cfg: RailsConfig, binary: list[str] | None = None) -> None:
        self.cfg = cfg
        self.binary = binary if binary is not None else self.default_binary()

    def default_binary(self) -> list[str]:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError

    def build_argv(
        self, prompt: str, *, cwd: Path, out_file: Path
    ) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _parse(self, lines: list[str], *, cwd: Path, out_file: Path) -> ParsedTranscript:
        """Default: nothing engine-specific to extract."""
        return ParsedTranscript()

    def run(
        self,
        prompt: str,
        *,
        cwd: Path,
        timeout_s: int = 1800,
        extra_env: dict[str, str] | None = None,
    ) -> SessionResult:
        cwd = Path(cwd)
        transcript_dir = cwd / ".rails-transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{self.name}"
        transcript_path = transcript_dir / f"{stem}.jsonl"
        stderr_path = transcript_dir / f"{stem}.stderr.log"
        out_file = transcript_dir / f"{stem}.out"

        argv = self.build_argv(prompt, cwd=cwd, out_file=out_file)
        env = self.cfg.allowed_env(extra_env)

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # errors="replace", never strict: engines can emit raw
                # non-UTF-8 bytes mid-stream; a strict decoder would kill
                # the pump thread and silently truncate the transcript.
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            raise SessionError(f"failed to start {self.name} session: {exc}") from exc

        raw_lines: list[str] = []
        pump_errors: list[BaseException] = []

        def _drain(stream: IO[str], sink: IO[str], collect: list[str] | None) -> None:
            def write_line(line: str) -> None:
                # Guard against writes after the `with` block closed the
                # sink (a straggling thread on the timeout path).
                if not sink.closed:
                    sink.write(line + "\n")
                    sink.flush()
                if collect is not None:
                    collect.append(line)

            try:
                _pump_stream(stream, write_line)
            except Exception as exc:  # surfaced after wait -- never swallowed
                pump_errors.append(exc)

        with (
            open(transcript_path, "w", encoding="utf-8") as transcript_f,
            open(stderr_path, "w", encoding="utf-8") as stderr_f,
        ):
            # Each thread owns exactly one file, so no cross-thread locking
            # is needed. daemon=True: a pump wedged on a never-closing pipe
            # must not block interpreter exit.
            stdout_thread = threading.Thread(
                target=_drain, args=(proc.stdout, transcript_f, raw_lines), daemon=True
            )
            stderr_thread = threading.Thread(
                target=_drain, args=(proc.stderr, stderr_f, None), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()

            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc)
                proc.wait()
                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)
                raise SessionError(
                    f"{self.name} session exceeded timeout of {timeout_s}s (pid {proc.pid} killed)"
                ) from None

            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

        if pump_errors:
            # A truncated drain must never masquerade as a good run.
            raise SessionError(
                f"{self.name} transcript capture failed: {pump_errors[0]!r}"
            ) from pump_errors[0]

        duration_s = time.monotonic() - start
        parsed = self._parse(raw_lines, cwd=cwd, out_file=out_file)
        # ok semantics: exit 0 + no in-band error (explicit_result never changes ok).
        ok = proc.returncode == 0 and parsed.result_ok
        explicit_result = parsed.saw_result if self.emits_terminal_result else True

        return SessionResult(
            engine=self.name,
            ok=ok,
            final_message=parsed.final_message,
            transcript_path=transcript_path,
            duration_s=duration_s,
            cost_usd=parsed.cost_usd,
            raw_exit_code=proc.returncode,
            explicit_result=explicit_result,
        )
