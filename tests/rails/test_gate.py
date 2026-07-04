"""Tests for rails.gate: structured multi-step gate runner.

All tests here use an INJECTED fake step list (a `sys.executable -c ...`
argv -- portable, no reliance on PATH-resolved tools) rather than the real
6-step DEFAULT_STEPS, which need Postgres + a full node_modules install and
take minutes. The one exception is `test_real_gate_passes_against_repo_root`,
gated behind RAILS_REAL_GATE=1 and skipped by default -- see its docstring.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time

import pytest

from rails.gate import GateResult, StepResult, run_gate


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# --- all-pass / any-fail -----------------------------------------------------


def test_all_pass_gives_ok_true(tmp_path):
    steps = (
        ("a", _py("print('a-ok')")),
        ("b", _py("print('b-ok')")),
    )

    result = run_gate(tmp_path, steps=steps)

    assert result.ok is True
    assert len(result.steps) == 2
    assert all(step.ok for step in result.steps)
    assert result.failed_steps() == ()


def test_one_failure_gives_ok_false_and_failed_steps(tmp_path):
    steps = (
        ("good", _py("print('fine')")),
        ("bad", _py("import sys; print('boom', file=sys.stderr); sys.exit(1)")),
    )

    result = run_gate(tmp_path, steps=steps)

    assert result.ok is False
    failed = result.failed_steps()
    assert [s.name for s in failed] == ["bad"]
    assert failed[0].exit_code == 1


def test_all_steps_run_even_after_an_earlier_failure(tmp_path):
    """No stop-on-first-failure: a step after a red one must still run (and
    its result recorded) so the agent sees the full picture in one pass."""
    steps = (
        ("bad", _py("import sys; sys.exit(1)")),
        ("good", _py("print('still ran')")),
    )

    result = run_gate(tmp_path, steps=steps)

    assert len(result.steps) == 2
    assert result.steps[0].ok is False
    assert result.steps[1].name == "good"
    assert result.steps[1].ok is True


# --- summary() ---------------------------------------------------------------


def test_summary_contains_failing_step_name_and_output_tail(tmp_path):
    steps = (("bad", _py("print('DISTINCTIVE_FAILURE_TEXT'); import sys; sys.exit(1)")),)

    result = run_gate(tmp_path, steps=steps)
    summary = result.summary()

    assert "bad" in summary
    assert "DISTINCTIVE_FAILURE_TEXT" in summary
    assert "✗" in summary  # ✗


def test_summary_shows_pass_marker_for_ok_steps(tmp_path):
    steps = (("good", _py("print('ok')")),)

    result = run_gate(tmp_path, steps=steps)

    assert "✓" in result.summary()  # ✓


def test_summary_omits_passing_step_output(tmp_path):
    steps = (
        ("good", _py("print('QUIET_PASS_OUTPUT')")),
        ("bad", _py("import sys; sys.exit(1)")),
    )

    result = run_gate(tmp_path, steps=steps)
    summary = result.summary()

    assert "QUIET_PASS_OUTPUT" not in summary


# --- durations / output capture ---------------------------------------------


def test_durations_are_recorded(tmp_path):
    steps = (("sleepy", _py("import time; time.sleep(0.2)")),)

    result = run_gate(tmp_path, steps=steps)

    assert result.steps[0].duration_s >= 0.2


def test_output_tail_is_truncated_to_last_2000_chars(tmp_path):
    steps = (("chatty", _py("print('x' * 5000)")),)

    result = run_gate(tmp_path, steps=steps)

    assert len(result.steps[0].output_tail) <= 2000
    assert result.steps[0].output_tail.rstrip().endswith("x")


def test_pytest_step_keeps_larger_tail_budget(tmp_path):
    """A step NAMED 'pytest' gets a 6000-char tail so a failing assertion +
    short traceback survives truncation for the retry prompt; a non-pytest
    step emitting the same volume stays clamped to the 2000 default."""
    big = "print('y' * 8000)"
    steps = (
        ("pytest", _py(big)),
        ("ruff-check", _py(big)),
    )

    result = run_gate(tmp_path, steps=steps)

    pytest_tail = result.steps[0].output_tail
    ruff_tail = result.steps[1].output_tail
    assert 2000 < len(pytest_tail) <= 6000
    assert len(ruff_tail) <= 2000


def test_env_param_is_passed_to_steps(tmp_path):
    steps = (("env-check", _py("import os; print(os.environ.get('RAILS_TEST_VAR', 'MISSING'))")),)

    result = run_gate(tmp_path, steps=steps, env={"RAILS_TEST_VAR": "hello"})

    assert "hello" in result.steps[0].output_tail


def test_env_merge_preserves_inherited_os_environ(tmp_path, monkeypatch):
    """The env merge is {**os.environ, **env}, NOT a replace: a caller
    passing env extras must NOT wipe out the inherited environment (a replace
    would drop PATH and break every step). Pin it with a canary in
    os.environ that must still be visible to a step even when `env` is set."""
    monkeypatch.setenv("RAILS_INHERITED_CANARY", "still-here")
    steps = (
        ("env-check", _py("import os; print(os.environ.get('RAILS_INHERITED_CANARY', 'GONE'))")),
    )

    result = run_gate(tmp_path, steps=steps, env={"OTHER": "x"})

    assert "still-here" in result.steps[0].output_tail


# --- DEFAULT_STEPS argv --------------------------------------------------------


def test_default_steps_pytest_uses_tb_short():
    """The pytest step must pass --tb=short so a failing assertion's actual
    message survives (a long default traceback would get truncated away from
    the tail that feeds the retry prompt)."""
    from rails.gate import DEFAULT_STEPS

    pytest_argv = next(argv for name, argv in DEFAULT_STEPS if name == "pytest")
    assert "--tb=short" in pytest_argv


# --- total_timeout_s enforcement ---------------------------------------------


def test_total_timeout_marks_slow_step_failed_without_hanging(tmp_path):
    steps = (("forever", _py("import time; time.sleep(30)")),)

    start = time.monotonic()
    result = run_gate(tmp_path, steps=steps, total_timeout_s=1)
    elapsed = time.monotonic() - start

    assert elapsed < 15  # did NOT wait out the full 30s sleep
    assert result.ok is False
    assert result.steps[0].ok is False


def test_total_timeout_skips_remaining_steps_once_budget_exhausted(tmp_path):
    steps = (
        ("slow", _py("import time; time.sleep(2)")),
        ("never-runs", _py("print('should not run')")),
    )

    result = run_gate(tmp_path, steps=steps, total_timeout_s=1)

    assert len(result.steps) == 2
    assert result.steps[0].ok is False
    assert result.steps[1].ok is False


# --- frozen dataclasses -------------------------------------------------------


def test_step_result_and_gate_result_are_frozen():
    step = StepResult(name="x", ok=True, exit_code=0, duration_s=0.1, output_tail="")
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.ok = False

    gate = GateResult(ok=True, steps=(step,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        gate.ok = False


# --- real gate (opt-in) -------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RAILS_REAL_GATE") != "1",
    reason="set RAILS_REAL_GATE=1 to run the real 6-step gate against the repo root "
    "(slow; needs local Postgres on 54322 and web/ deps installed)",
)
def test_real_gate_passes_against_repo_root():
    """Runs the REAL DEFAULT_STEPS against the actual repo root -- proves the
    argv in DEFAULT_STEPS are correct, not just the injected-fake-step
    machinery. Skipped by default.

    RAILS_REAL_GATE=1 must NOT reach the gate's own `pytest` step: `env=None`
    would inherit it into that nested `uv run pytest -q` (full suite), which
    would collect THIS test un-skipped and recurse -- each level spawning
    another whole nested pytest run inside its own `pytest` step, without
    bound (verified: this is exactly what happens if the override below is
    removed -- the run explodes into dozens of stacked processes and never
    returns). Force it to "0" for the nested run to break the cycle.
    """
    from rails.config import RailsConfig

    cfg = RailsConfig.load()

    result = run_gate(cfg.repo_root, total_timeout_s=1800, env={"RAILS_REAL_GATE": "0"})

    assert result.ok, result.summary()
