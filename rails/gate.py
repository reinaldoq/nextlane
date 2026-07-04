"""Deterministic gate runner: the same lint/test/build steps the justfile's
`gate` recipe runs, executed as discrete subprocesses so a failure comes
back as a structured, per-step result instead of one opaque shell exit code.

Design notes:
- ALL steps run, even once an earlier one has failed. Stopping at the first
  red step would hide unrelated failures (e.g. a real web-build break
  sitting behind an already-known ruff violation); an agent retrying
  against a red gate wants the full picture in one pass, not one problem
  revealed per retry. The tradeoff is wall-clock, so `total_timeout_s` is
  enforced across the WHOLE run (not per step): once the budget is spent,
  remaining steps are recorded as failed rather than started, so a wedged
  step can never burn the entire session's time budget.
- `run_gate` is meant to run inside a WORKTREE (see rails/worktree.py), not
  the main checkout, so a running gate never interferes with -- or is
  interfered with by -- other work in progress. It assumes the same
  environment `just gate` assumes: local Postgres up on 54322 for the
  pytest step (tests/conftest.py's DATABASE_URL setdefault), web deps
  installed (rails/worktree.py's provisioning step), Node + npm on PATH.
  The `env` param lets a caller (Task 6's loop) inject DATABASE_URL or
  anything else on top of the inherited environment, without mutating the
  parent process's `os.environ`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_TAIL_CHARS = 2000

# Mirrors the justfile's `gate` recipe (lint + test + web build), as
# discrete argv steps rather than a single `just gate` shell-out, so each
# one gets its own structured StepResult.
DEFAULT_STEPS: tuple[tuple[str, list[str]], ...] = (
    ("ruff-check", ["uv", "run", "ruff", "check", "."]),
    ("ruff-format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("pytest", ["uv", "run", "pytest", "-q"]),
    ("web-lint", ["npm", "--prefix", "web", "run", "lint"]),
    ("web-typecheck", ["npm", "--prefix", "web", "run", "typecheck"]),
    ("web-build", ["npm", "--prefix", "web", "run", "build"]),
)


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    exit_code: int
    duration_s: float
    output_tail: str  # last ~2000 chars of combined stdout+stderr


@dataclass(frozen=True)
class GateResult:
    ok: bool
    steps: tuple[StepResult, ...]

    def failed_steps(self) -> tuple[StepResult, ...]:
        return tuple(step for step in self.steps if not step.ok)

    def summary(self) -> str:
        """Human/agent-readable report: one line per step (✓/✗ + duration),
        then the failing steps' output tail -- this feeds the retry prompt
        in Task 6's loop."""
        lines = [
            f"{'✓' if step.ok else '✗'} {step.name} ({step.duration_s:.1f}s)" for step in self.steps
        ]
        failed = self.failed_steps()
        if failed:
            lines.append("")
            lines.append("--- failing step output (tail) ---")
            for step in failed:
                lines.append(f"\n# {step.name} (exit {step.exit_code})")
                lines.append(step.output_tail)
        return "\n".join(lines)


def _run_step(
    name: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout_s: float,
) -> StepResult:
    start = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        output = result.stdout or ""
        return StepResult(
            name=name,
            ok=result.returncode == 0,
            exit_code=result.returncode,
            duration_s=time.monotonic() - start,
            output_tail=output[-_TAIL_CHARS:],
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        tail = output[-_TAIL_CHARS:] if output else ""
        return StepResult(
            name=name,
            ok=False,
            exit_code=-1,
            duration_s=time.monotonic() - start,
            output_tail=(tail + "\n[step timed out]") if tail else "[step timed out]",
        )
    except OSError as exc:
        return StepResult(
            name=name,
            ok=False,
            exit_code=-1,
            duration_s=time.monotonic() - start,
            output_tail=f"[failed to start: {exc}]",
        )


def run_gate(
    cwd: Path,
    *,
    steps: tuple[tuple[str, list[str]], ...] = DEFAULT_STEPS,
    total_timeout_s: int = 1800,
    env: dict[str, str] | None = None,
) -> GateResult:
    """Run `steps` in order (cwd=cwd), never stopping early on a failure.

    `total_timeout_s` is a wall-clock budget across ALL steps combined: once
    it's exhausted, any not-yet-started step is recorded as a failed/skipped
    StepResult rather than run. `env` extras are merged on top of the
    inherited environment (never replacing it -- steps still need PATH etc).
    """
    merged_env = {**os.environ, **env} if env is not None else None

    results: list[StepResult] = []
    start_all = time.monotonic()

    for name, argv in steps:
        budget_remaining = total_timeout_s - (time.monotonic() - start_all)
        if budget_remaining <= 0:
            results.append(
                StepResult(
                    name=name,
                    ok=False,
                    exit_code=-1,
                    duration_s=0.0,
                    output_tail="[skipped: total_timeout_s budget exhausted before this step started]",
                )
            )
            continue
        results.append(_run_step(name, argv, cwd=cwd, env=merged_env, timeout_s=budget_remaining))

    return GateResult(ok=all(step.ok for step in results), steps=tuple(results))
