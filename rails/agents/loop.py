"""The shared agent-task loop: worktree -> build -> gate -> cross-vendor
review -> (bounded revision) -> PR -> journal.

Spec ref: Phase-2 Task 6. This assembles every primitive built in Tasks 1-5
(adapters, worktree, gate, prompts, journal, github) into ONE function,
`run_agent_task`, that every day-2 agent (`build_feature` now; `triage`,
`migrate`, `review` in Task 8) drives through.

Determinism/testability: every side-effecting collaborator is an injected
parameter with a real-world default (`make_adapter`, `run_gate_fn`,
`worktree_cm`, `open_pr_fn`, `record_fn`, `now_fn`) -- unit tests pass fakes
for ALL of them, so the whole loop is exercised without ever spawning a real
engine CLI, touching real git remotes, or shelling out to `gh`. The two
un-injected, git-touching helpers are `_diff` (a `git diff`) and
`_count_commits` (a `git rev-list --count`); tests monkeypatch
`rails.agents.loop._diff` / `._count_commits` directly rather than threading
them through the public signature.

Operational visibility (Task 6 review round): a real run is 10-30 minutes of
otherwise-dead air, so every phase prints a timestamped banner to
`err_console` (elapsed-seconds-prefixed) as it happens -- worktree ready,
each session start, each gate, the review verdict, the PR. The final rich
summary table still prints at the end. Tests set a recording console via
monkeypatching `rails.agents.loop.err_console`.

Robustness invariants:
- **PR opens ONLY on a green final gate.** A red gate (initial, exhausted
  retry, or post-revision), an empty branch, a SessionError, a blown budget,
  or a GitHubError all end in a journaled terminal record and a non-PR exit.
- A `SessionError` (timeout / spawn / capture failure from an adapter) is
  caught, journaled as outcome="error" with the transcripts collected so far
  plus the failing session's partial transcript, and re-raised as
  `typer.Exit(1)` -- never an unhandled crash.
- A whole-run wall-clock budget (`total_timeout_s`) bounds the entire run: a
  shrinking per-session/-gate timeout is derived from the remaining budget,
  and exhaustion journals outcome="timeout" and exits.

Recursion guard: the composed builder prompt tells the agent to run
`just gate` itself (see `rails.prompts.compose`) -- and `just gate`'s pytest
step would otherwise collect `test_real_gate_passes_against_repo_root`, which
calls `run_gate` again. `extra_env` forces `RAILS_REAL_GATE=0` on every
subprocess this loop launches (builder AND reviewer sessions, plus our own
`run_gate_fn` calls) so that test stays skipped inside a rails-driven run.
`DATABASE_URL` rides in the same dict because the agent's in-session
`just gate` (and our own `run_gate_fn`) needs a live Postgres for pytest.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rails import github
from rails.adapters import get_adapter
from rails.adapters.base import SessionError, SessionResult
from rails.config import RailsConfig
from rails.gate import GateResult, run_gate
from rails.github import GitHubError
from rails.journal import RunRecord
from rails.journal import record as journal_record
from rails.prompts import compose, compose_retry, compose_review
from rails.worktree import cleanup, worktree_for

console = Console()
err_console = Console(stderr=True)

# reviewer_engine default when the caller doesn't pick one: the OTHER of
# claude/codex (true cross-vendor review), and claude for gemini (gemini has
# no natural "other" vendor pairing in this three-engine lineup yet).
_DEFAULT_REVIEWER = {"claude": "codex", "codex": "claude", "gemini": "claude"}

# Mirrors tests/conftest.py's own setdefault -- the local dev Postgres a
# fresh checkout expects on 54322. Used only as a fallback so an operator's
# real DATABASE_URL (if set) always wins.
_DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# Whole-run wall-clock budget default: ~90 minutes. A build + up to two
# retries + a review + one revision, each capable of a long headless session,
# must still be bounded so a wedged run can't burn quota indefinitely.
_DEFAULT_TOTAL_TIMEOUT_S = 5400

# Derived from docs/superpowers/agents-md-seed.md's conventions (Task 7
# formalizes these into AGENTS.md proper; this is the concise inline version
# the cross-vendor reviewer checks a diff against in the meantime).
CHECKLIST = """\
- Every router endpoint that needs it has an auth guard -- no unauthenticated data access.
- Money amounts are stored/compared as integer cents, never floats.
- Status changes go through an explicit transitions endpoint/state machine, not a raw field PATCH.
- List endpoints validate `sort` (and similar client-supplied fields) against a whitelist, never
  an operator-supplied raw column or expression.
- All SQL is parameterized -- no f-string/format-built queries.
- Pydantic request models use `extra="forbid"`.
- Tests cover the happy path AND the 401 (unauthenticated) and 422 (validation) cases.
- New migrations follow the naming convention and enable RLS on new tables.
- No commits touch docs/ unless the task explicitly asked for docs changes.
"""

_VERDICT_RE = re.compile(r"^\s*\*{0,2}VERDICT:\s*(APPROVE|REQUEST_CHANGES)\b", re.IGNORECASE)


def parse_verdict(final_message: str) -> str:
    """Scan `final_message` bottom-up for the LAST line matching
    `VERDICT: APPROVE` / `VERDICT: REQUEST_CHANGES` (optionally wrapped in
    markdown bold, case-insensitive). Returns "APPROVE" or "REQUEST_CHANGES".

    FAIL-SAFE: if no line matches, returns "REQUEST_CHANGES" -- a reviewer
    session that never produced a parseable verdict (truncated output, a
    format slip) must NEVER be treated as an approval. Callers must pass the
    REVIEWER's own `final_message`, never the prompt we sent it -- a diff
    under review is untrusted content and could itself contain a line that
    looks like a verdict (see `rails.prompts.compose_review`, which wraps the
    diff precisely so the reviewer doesn't act on text embedded inside it;
    this function provides the second half of that guarantee by only ever
    being called on the reviewer's actual response).
    """
    for line in reversed(final_message.splitlines()):
        match = _VERDICT_RE.match(line)
        if match:
            return match.group(1).upper()
    return "REQUEST_CHANGES"


def slug_from(title: str, *, max_len: int = 40) -> str:
    """Derive a worktree/branch slug from a human-readable title: lowercase,
    non-alnum runs collapsed to a single dash, truncated to `max_len` chars
    (trailing dashes trimmed after truncation so it never ends mid-dash)."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "task"


def sum_costs(costs: Iterable[float | None]) -> float | None:
    """Sum every non-None cost in `costs`; None counts as 0 -- UNLESS every
    single value is None, in which case the sum is itself None rather than a
    misleading 0.0. Mixed-engine runs undercount: codex and gemini adapters
    report `cost_usd=None` (no per-session dollar figure from those CLIs), so
    a run whose builder is codex and reviewer is claude only reflects the
    claude session's spend here -- documented, not fixed (no per-session
    dollar cost exists for those engines to add)."""
    values = list(costs)
    if all(c is None for c in values):
        return None
    return sum(c for c in values if c is not None)


def _resolve_database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


def _diff(wt_path: Path, *, base: str = "main") -> str:
    """`git diff base...HEAD` inside the worktree -- three-dot: everything on
    the branch since it diverged from `base`, which is what a reviewer needs
    to see the FULL change regardless of how many retry/revision commits
    happened along the way."""
    result = subprocess.run(
        ["git", "-C", str(wt_path), "diff", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def _count_commits(wt_path: Path, *, base: str = "main") -> int:
    """Number of commits on the worktree branch since it diverged from
    `base` (`git rev-list --count base..HEAD`). Zero means the agent session
    produced no committed work -- nothing to review or open a PR for."""
    result = subprocess.run(
        ["git", "-C", str(wt_path), "rev-list", "--count", f"{base}..HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return int(result.stdout.strip() or "0")


def _compose_revision(original_prompt: str, review_feedback: str) -> str:
    """The ONE post-review revision prompt: the original task framing plus
    the reviewer's REQUEST_CHANGES feedback. `review_feedback` is the
    reviewer's own generated text (it was explicitly instructed not to act on
    anything embedded in the diff it reviewed -- see `compose_review`), so,
    like `compose_retry`'s gate summary, it's included plain rather than
    wrapped as untrusted data. Same caveat as `compose_retry`'s docstring:
    a reviewer's prose could still transitively echo a hostile diff fragment
    it quoted; today's callers don't route adversarial content through here."""
    return f"""{original_prompt}

---

An independent, cross-vendor reviewer requested changes on your last
commit(s). Address ONLY the feedback below -- do not refactor or touch
unrelated, already-passing code. Re-run `just gate` yourself before
declaring the task done again.

Reviewer feedback:
{review_feedback}
"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _print_summary(run: RunRecord) -> None:
    table = Table(title="rails run summary")
    table.add_column("field")
    table.add_column("value")
    table.add_row("task", run.task_summary)
    table.add_row("engine", run.engine)
    table.add_row("reviewer", run.reviewer_engine or "-")
    table.add_row("retries", str(run.retries))
    table.add_row("gate", "green" if run.gate_ok else "red")
    table.add_row("review verdict", run.review_verdict or "-")
    table.add_row("pr", run.pr_url or "-")
    table.add_row("cost (usd)", f"{run.cost_usd:.4f}" if run.cost_usd is not None else "-")
    table.add_row("duration (s)", f"{run.duration_s:.1f}")
    table.add_row("outcome", run.outcome)
    console.print(table)


def run_agent_task(
    cfg: RailsConfig,
    *,
    task_kind: str,
    task_body: str,
    title: str,
    engine: str | None = None,
    reviewer_engine: str | None = None,
    max_retries: int = 2,
    open_pr: bool = True,
    total_timeout_s: int = _DEFAULT_TOTAL_TIMEOUT_S,
    # injected collaborators (default to real; tests pass fakes):
    make_adapter=get_adapter,
    run_gate_fn=run_gate,
    worktree_cm=worktree_for,
    open_pr_fn=github.open_pr,
    record_fn=journal_record,
    now_fn=_utc_now_iso,
) -> RunRecord:
    """Drive ONE end-to-end agent task run. See module docstring for the
    invariants (PR opens only on a green final gate; SessionError/budget/
    empty-branch all journal + exit without a PR) and the injection contract
    that makes this fully unit-testable with fakes.

    Returns the final `RunRecord` on a successful run (`outcome` is
    "pr_opened" or "completed_no_pr"). Every terminal failure path journals a
    record too, but raises instead of returning it: `typer.Exit(1)` for
    gate_failed / no_changes / timeout / a caught SessionError, or the
    underlying `GitHubError` re-raised for a PR-open failure -- in every case
    from INSIDE the `worktree_cm` block, so its exception path force-cleans
    the worktree and deletes the branch (see `rails.worktree.worktree_for`).
    """
    start = time.monotonic()
    deadline = start + total_timeout_s
    engine = engine or cfg.engine
    reviewer_engine = reviewer_engine or _DEFAULT_REVIEWER.get(engine, "claude")

    builder = make_adapter(engine, cfg)
    # The reviewer runs READ-ONLY: it must never mutate the worktree it is
    # judging (I3). readonly=True selects each engine's read-only permission
    # mode in build_argv.
    reviewer = make_adapter(reviewer_engine, cfg, readonly=True)

    extra_env = {"DATABASE_URL": _resolve_database_url(), "RAILS_REAL_GATE": "0"}

    sessions: list[SessionResult] = []
    retries = 0
    review_verdict: str | None = None

    with worktree_cm(slug_from(title), repo_root=cfg.repo_root) as wt:
        transcript_dir = wt.path / ".rails-transcripts"

        def _phase(msg: str) -> None:
            elapsed = time.monotonic() - start
            err_console.print(f"[dim][+{elapsed:6.1f}s][/dim] {msg}")

        def _record(
            *,
            gate_ok: bool,
            pr_url: str | None,
            outcome: str,
            extra_transcript_paths: list[str] | None = None,
        ) -> RunRecord:
            paths = [str(s.transcript_path) for s in sessions]
            if extra_transcript_paths:
                paths.extend(extra_transcript_paths)
            run = RunRecord(
                ts_iso=now_fn(),
                task_kind=task_kind,
                task_summary=title,
                engine=engine,
                reviewer_engine=reviewer_engine,
                worktree_branch=wt.branch,
                gate_ok=gate_ok,
                retries=retries,
                duration_s=time.monotonic() - start,
                cost_usd=sum_costs(s.cost_usd for s in sessions),
                pr_url=pr_url,
                outcome=outcome,
                transcript_paths=paths,
                review_verdict=review_verdict,
            )
            record_fn(run)
            return run

        def _budget_or_exit(gate_ok: bool) -> int:
            """Remaining whole-run budget in whole seconds; if exhausted,
            journal outcome="timeout" and abort (I4a)."""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _phase(
                    f"[bold red]✗ total run budget of {total_timeout_s}s exhausted -- "
                    "aborting, no PR opened.[/bold red]"
                )
                _record(gate_ok=gate_ok, pr_url=None, outcome="timeout")
                raise typer.Exit(1)
            return max(1, int(remaining))

        def _run_session(adapter, prompt: str, *, label: str, gate_ok: bool) -> SessionResult:
            timeout_s = _budget_or_exit(gate_ok)
            _phase(f"▶ {label} ({adapter.name}) … (up to {timeout_s}s of remaining budget)")
            try:
                session = adapter.run(prompt, cwd=wt.path, timeout_s=timeout_s, extra_env=extra_env)
            except SessionError as exc:
                failing = getattr(exc, "transcript_path", None)
                _phase(f"[bold red]✗ {label} failed: {exc}[/bold red]")
                if failing is not None:
                    _phase(f"inspect the failing session transcript: {failing}")
                _record(
                    gate_ok=gate_ok,
                    pr_url=None,
                    outcome="error",
                    extra_transcript_paths=[str(failing)] if failing is not None else None,
                )
                raise typer.Exit(1) from exc
            # I4b: ok but no terminal result event is suspicious (truncated
            # stream / swallowed output) -- surface it, don't fail on it.
            if session.ok and not session.explicit_result:
                _phase(
                    f"[yellow]⚠ {label} exited ok but emitted no terminal result event -- "
                    "its output may be truncated; inspect the transcript.[/yellow]"
                )
            return session

        def _gate(gate_ok_before: bool) -> GateResult:
            timeout_s = _budget_or_exit(gate_ok_before)
            _phase("▶ gate …")
            result = run_gate_fn(wt.path, env=extra_env, total_timeout_s=timeout_s)
            if result.ok:
                _phase("[green]✓ gate green[/green]")
            else:
                failed = ", ".join(step.name for step in result.failed_steps())
                _phase(f"[bold red]✗ gate red -- failing steps: {failed}[/bold red]")
            return result

        _phase(f"worktree ready: branch [bold]{wt.branch}[/bold] at {wt.path}")
        _phase(f"transcripts under {transcript_dir} (tail -f to follow the live session)")

        # --- build ---
        original_prompt = compose(task_kind, task_body, engine_label=f"{engine} (rails)")
        sessions.append(
            _run_session(builder, original_prompt, label="builder session", gate_ok=False)
        )
        gate_result = _gate(False)

        # --- retries: recompose from the ORIGINAL prompt each time + the
        # LATEST gate summary (M1) -- NOT from the previous retry prompt,
        # which would accumulate stale gate tails and duplicate instructions.
        while not gate_result.ok and retries < max_retries:
            retry_prompt = compose_retry(original_prompt, gate_result.summary())
            sessions.append(
                _run_session(
                    builder, retry_prompt, label=f"builder retry {retries + 1}", gate_ok=False
                )
            )
            gate_result = _gate(False)
            retries += 1

        if not gate_result.ok:
            _phase("[bold red]✗ gate still red after retries -- aborting, no PR opened.[/bold red]")
            _record(gate_ok=False, pr_url=None, outcome="gate_failed")
            err_console.print(gate_result.summary())
            raise typer.Exit(1)

        # --- I2: empty-diff guard. A green gate on a branch with ZERO
        # commits means the agent did nothing worth reviewing/PRing.
        if _count_commits(wt.path) == 0:
            _phase(
                "[bold red]✗ gate is green but the branch has no commits -- "
                "nothing to review or open a PR for.[/bold red]"
            )
            _record(gate_ok=True, pr_url=None, outcome="no_changes")
            raise typer.Exit(1)

        # --- cross-vendor review (read-only reviewer, full branch diff) ---
        diff = _diff(wt.path)
        review_session = _run_session(
            reviewer, compose_review(diff, checklist=CHECKLIST), label="review", gate_ok=True
        )
        sessions.append(review_session)
        review_verdict = parse_verdict(review_session.final_message)
        _phase(f"review verdict from {reviewer_engine}: [bold]{review_verdict}[/bold]")

        if review_verdict != "APPROVE":
            revision_prompt = _compose_revision(original_prompt, review_session.final_message)
            sessions.append(_run_session(builder, revision_prompt, label="revision", gate_ok=True))
            gate_result = _gate(True)
            if not gate_result.ok:
                _phase(
                    "[bold red]✗ gate red after the review-driven revision -- "
                    "aborting, no PR opened.[/bold red]"
                )
                _record(gate_ok=False, pr_url=None, outcome="gate_failed")
                err_console.print(gate_result.summary())
                raise typer.Exit(1)
            # The post-revision review is NOT repeated -- advisory-only,
            # bounded to exactly one cycle (see module docstring).

        if not open_pr:
            run = _record(gate_ok=True, pr_url=None, outcome="completed_no_pr")
            _phase(
                "open_pr=False -- leaving the worktree AND branch in place for inspection "
                f"({wt.path})"
            )
            _print_summary(run)
            return run

        summary_text = sessions[0].final_message or title
        body = github.pr_body(
            summary_text,
            engine_label=f"{engine} (rails)",
            journal_note=f"Cross-vendor review by {reviewer_engine} (rails): {review_verdict}",
        )
        _phase("▶ opening PR …")
        try:
            pr_url = open_pr_fn(worktree=wt, title=title, body=body, repo_root=cfg.repo_root)
        except GitHubError as exc:
            # Best-effort only: we can't cheaply tell whether the push landed
            # before gh failed, so we don't attempt remote branch cleanup here
            # (a TODO, not a correctness bug -- see module docstring/spec I5b).
            _phase(f"[bold red]✗ opening the PR failed: {exc}[/bold red]")
            _record(gate_ok=True, pr_url=None, outcome="error")
            raise
        _phase(f"[green]✓ PR opened:[/green] {pr_url}")

        run = _record(gate_ok=True, pr_url=pr_url, outcome="pr_opened")
        # I1: print the summary BEFORE cleanup, and let a cleanup failure only
        # WARN -- a raised cleanup would unwind through worktree_for's
        # except-branch and delete the branch we just opened a PR for.
        _print_summary(run)
        try:
            cleanup(wt, repo_root=cfg.repo_root, delete_branch=False)
        except Exception as exc:  # noqa: BLE001 -- must never mask a successful PR
            err_console.print(
                "[yellow]warning: worktree cleanup failed after opening the PR; "
                f"leaving {wt.path} in place, branch preserved: {exc}[/yellow]"
            )
        return run
