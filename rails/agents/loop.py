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
engine CLI, touching real git remotes, or shelling out to `gh`. The one
un-injected, git-touching helper is `_diff` (a plain `git diff` invocation);
tests monkeypatch `rails.agents.loop._diff` directly rather than threading it
through the public signature below, which matches the exact contract this
task was scoped against.

Invariant (repeated at each decision point below): **a PR opens ONLY on a
green final gate.** A red gate, whether from the initial build, an exhausted
retry budget, or a review-driven revision, always ends in outcome
"gate_failed", a journal entry, and `typer.Exit(1)` -- never a PR.

Recursion guard: the composed builder prompt tells the agent to run
`just gate` itself before declaring the task done (see `rails.prompts.compose`
docstring) -- and `just gate`'s `pytest` step would otherwise collect and run
`tests/rails/test_gate.py::test_real_gate_passes_against_repo_root`, which
itself calls `run_gate` again. `extra_env` forces `RAILS_REAL_GATE=0` on every
subprocess this loop launches (builder AND reviewer sessions, plus our own
`run_gate_fn` calls) so that test stays skipped inside a rails-driven run,
exactly as it already does for CI's own gate run (see that test's docstring).
`DATABASE_URL` is included in the same `extra_env` dict because the agent's
own in-session `just gate` needs a live Postgres for its `pytest` step, same
as our own `run_gate_fn` call does.
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
from rails.adapters.base import SessionResult
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
    # injected collaborators (default to real; tests pass fakes):
    make_adapter=get_adapter,
    run_gate_fn=run_gate,
    worktree_cm=worktree_for,
    open_pr_fn=github.open_pr,
    record_fn=journal_record,
    now_fn=_utc_now_iso,
) -> RunRecord:
    """Drive ONE end-to-end agent task run. See module docstring for the
    invariant (PR opens only on a green final gate) and the injection
    contract that makes this fully unit-testable with fakes.

    Returns the final `RunRecord` on a successful run (`outcome` is
    "pr_opened" or "completed_no_pr"). On a failed run (`outcome` is
    "gate_failed" or "error") the record is journaled but this function never
    returns it -- it raises `typer.Exit(1)` (gate_failed) or re-raises the
    underlying `GitHubError` (error) instead, in both cases from INSIDE the
    `worktree_cm` block so its exception path force-cleans the worktree and
    deletes the branch (see `rails.worktree.worktree_for`).
    """
    start = time.monotonic()
    engine = engine or cfg.engine
    reviewer_engine = reviewer_engine or _DEFAULT_REVIEWER.get(engine, "claude")

    builder = make_adapter(engine, cfg)
    reviewer = make_adapter(reviewer_engine, cfg)

    extra_env = {"DATABASE_URL": _resolve_database_url(), "RAILS_REAL_GATE": "0"}

    sessions: list[SessionResult] = []
    retries = 0
    review_verdict: str | None = None

    with worktree_cm(slug_from(title), repo_root=cfg.repo_root) as wt:

        def _record(*, gate_ok: bool, pr_url: str | None, outcome: str) -> RunRecord:
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
                transcript_paths=[str(s.transcript_path) for s in sessions],
                review_verdict=review_verdict,
            )
            record_fn(run)
            return run

        original_prompt = compose(task_kind, task_body, engine_label=f"{engine} (rails)")
        prompt = original_prompt
        sessions.append(builder.run(prompt, cwd=wt.path, extra_env=extra_env))
        gate_result: GateResult = run_gate_fn(wt.path, env=extra_env)

        while not gate_result.ok and retries < max_retries:
            prompt = compose_retry(prompt, gate_result.summary())
            sessions.append(builder.run(prompt, cwd=wt.path, extra_env=extra_env))
            gate_result = run_gate_fn(wt.path, env=extra_env)
            retries += 1

        if not gate_result.ok:
            _record(gate_ok=False, pr_url=None, outcome="gate_failed")
            err_console.print(
                f"[bold red]Gate failed after {retries} retr"
                f"{'y' if retries == 1 else 'ies'} -- aborting, no PR opened.[/bold red]"
            )
            err_console.print(gate_result.summary())
            raise typer.Exit(1)

        # Green -- cross-vendor review. The reviewer sees the FULL diff since
        # the branch diverged from main, regardless of how many retries above
        # contributed to it.
        diff = _diff(wt.path)
        review_session = reviewer.run(
            compose_review(diff, checklist=CHECKLIST), cwd=wt.path, extra_env=extra_env
        )
        sessions.append(review_session)
        review_verdict = parse_verdict(review_session.final_message)

        if review_verdict != "APPROVE":
            revision_prompt = _compose_revision(original_prompt, review_session.final_message)
            sessions.append(builder.run(revision_prompt, cwd=wt.path, extra_env=extra_env))
            gate_result = run_gate_fn(wt.path, env=extra_env)
            if not gate_result.ok:
                _record(gate_ok=False, pr_url=None, outcome="gate_failed")
                err_console.print(
                    "[bold red]Gate failed after the review-driven revision -- "
                    "aborting, no PR opened.[/bold red]"
                )
                err_console.print(gate_result.summary())
                raise typer.Exit(1)
            # The post-revision review is NOT repeated -- advisory-only, bounded
            # to exactly one cycle (see module docstring).

        if not open_pr:
            run = _record(gate_ok=True, pr_url=None, outcome="completed_no_pr")
            _print_summary(run)
            return run

        summary_text = sessions[0].final_message or title
        body = github.pr_body(
            summary_text,
            engine_label=f"{engine} (rails)",
            journal_note=f"Cross-vendor review by {reviewer_engine} (rails): {review_verdict}",
        )
        try:
            pr_url = open_pr_fn(worktree=wt, title=title, body=body, repo_root=cfg.repo_root)
        except GitHubError:
            # Best-effort only: we can't cheaply tell whether the push landed
            # before gh failed, so we don't attempt remote branch cleanup here
            # (a TODO, not a correctness bug -- see module docstring/spec I5b).
            _record(gate_ok=True, pr_url=None, outcome="error")
            raise

        run = _record(gate_ok=True, pr_url=pr_url, outcome="pr_opened")
        # worktree_for preserves the worktree+branch on a clean exit (the PR
        # flow needed them to survive until push); now that the PR is open,
        # reclaim the worktree checkout but keep the (now-pushed/PR'd) branch.
        cleanup(wt, repo_root=cfg.repo_root, delete_branch=False)
        _print_summary(run)
        return run
