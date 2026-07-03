"""Shared subprocess-adapter machinery: the `AgentSession` protocol, the
`SessionResult` every adapter returns, and `_SubprocessAdapter`, the base
class real engine adapters (claude, codex, gemini) build on.

Spec §7 (Rails / agent security): every session subprocess's env is built
ONLY from `RailsConfig.allowed_env(extra_env)` -- never `os.environ`
wholesale. stdout/stderr are streamed line-by-line to a transcript file on
disk (under `<cwd>/.rails-transcripts/`) so a run can be inspected after the
fact even if a parser misses something. On timeout the whole process group
is killed (never just the direct child -- engine CLIs can spawn their own
subprocesses) and a `SessionError` is raised.
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
from typing import Protocol

from rails.config import RailsConfig


@dataclass
class SessionResult:
    """Outcome of one agent session.

    Note for Task 6's loop: `ok and not explicit_result` is suspicious --
    the process exited 0 but the engine never emitted its terminal result
    event (truncated stream, wrapper swallowing output, ...).
    """

    engine: str
    ok: bool  # process exit 0 (and, where the engine reports it, no in-band error)
    final_message: str  # engine's last assistant text
    transcript_path: Path  # raw event stream saved to disk
    duration_s: float
    cost_usd: float | None  # claude reports; others None
    raw_exit_code: int
    explicit_result: bool  # engine emitted its terminal result event (claude: `result`)


class SessionError(RuntimeError):
    """Raised on timeout or spawn failure (NOT on nonzero engine exit -- that's ok=False)."""


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


class _SubprocessAdapter:
    """Base class for engine adapters that drive a subprocess CLI.

    Subclasses implement:
      - `default_binary() -> list[str]`
      - `build_argv(prompt) -> list[str]`
      - `_parse(lines: list[str]) -> tuple[str, float | None, bool, bool]`
        returning (final_message, cost_usd, result_ok, saw_result), where
        result_ok reflects whatever the engine's own event stream says about
        success (default: always True -- exit code alone decides `ok`) and
        saw_result is whether the engine emitted its terminal result event
        (or that engine's equivalent).
    """

    name: str

    def __init__(self, cfg: RailsConfig, binary: list[str] | None = None) -> None:
        self.cfg = cfg
        self.binary = binary if binary is not None else self.default_binary()

    def default_binary(self) -> list[str]:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError

    def build_argv(self, prompt: str) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _parse(self, lines: list[str]) -> tuple[str, float | None, bool, bool]:
        """Default: nothing engine-specific to extract."""
        return "", None, True, False

    def run(
        self,
        prompt: str,
        *,
        cwd: Path,
        timeout_s: int = 1800,
        extra_env: dict[str, str] | None = None,
    ) -> SessionResult:
        argv = self.build_argv(prompt)
        env = self.cfg.allowed_env(extra_env)

        transcript_dir = Path(cwd) / ".rails-transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        transcript_path = transcript_dir / f"{ts}-{self.name}.jsonl"

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise SessionError(f"failed to start {self.name} session: {exc}") from exc

        raw_lines: list[str] = []
        write_lock = threading.Lock()

        with open(transcript_path, "w") as transcript_f:

            def _pump(stream, prefix: str = "") -> None:
                for raw_line in stream:
                    line = raw_line.rstrip("\n")
                    with write_lock:
                        transcript_f.write(f"{prefix}{line}\n")
                        transcript_f.flush()
                    if not prefix:
                        raw_lines.append(line)

            stdout_thread = threading.Thread(target=_pump, args=(proc.stdout,))
            stderr_thread = threading.Thread(target=_pump, args=(proc.stderr, "STDERR: "))
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

        duration_s = time.monotonic() - start
        final_message, cost_usd, result_ok, saw_result = self._parse(raw_lines)
        # ok semantics unchanged by explicit_result: exit 0 + no in-band error.
        ok = proc.returncode == 0 and result_ok

        return SessionResult(
            engine=self.name,
            ok=ok,
            final_message=final_message,
            transcript_path=transcript_path,
            duration_s=duration_s,
            cost_usd=cost_usd,
            raw_exit_code=proc.returncode,
            explicit_result=saw_result,
        )
