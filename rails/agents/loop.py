"""The shared agent-task loop: worktree -> build -> gate -> cross-vendor
review -> (bounded revision -> final review) -> PR -> journal.

Spec ref: Phase-2 Task 6. This assembles every primitive built in Tasks 1-5
(adapters, worktree, gate, prompts, journal, github) into ONE function,
`run_agent_task`, that every day-2 agent (`build_feature` now; `triage`,
`migrate`, `review` in Task 8) drives through.

Determinism/testability: every side-effecting collaborator is an injected
parameter with a real-world default (`make_adapter`, `run_gate_fn`,
`worktree_cm`, `open_pr_fn`, `record_fn`, `now_fn`) -- unit tests pass fakes
for ALL of them, so the whole loop is exercised without ever spawning a real
engine CLI, touching real git remotes, or shelling out to `gh`. The
un-injected, git-touching helpers are `_diff` (a `git diff`),
`_count_commits` (a `git rev-list --count`), `_has_uncommitted_changes` (a
`git status --porcelain`), and `_auto_commit` (a `git add -A && git commit`);
tests monkeypatch `rails.agents.loop._diff` / `._count_commits` /
`._has_uncommitted_changes` / `._auto_commit` directly rather than threading
them through the public signature.

Operational visibility (Task 6 review round): a real run is 10-30 minutes of
otherwise-dead air, so every phase prints a timestamped banner to
`err_console` (elapsed-seconds-prefixed) as it happens -- worktree ready,
each session start, each gate, the review verdict, the PR. The final rich
summary table still prints at the end. Tests set a recording console via
monkeypatching `rails.agents.loop.err_console`.

Mission Control telemetry (the one sanctioned post-Phase-1-freeze app
addition, design spec Sec6/Sec12): `_mc_start_run` / `_mc_step` / `_mc_finish`
mirror the phase banners above into `rails.mission_control`'s PostgREST
writes (`agent_runs`/`run_steps` on the HOSTED Supabase project) so the
deployed `/mission-control` page can show a run live while it's happening.
This is PURE OBSERVABILITY, never load-bearing: every one of the three
wrappers catches any exception from `rails.mission_control` (a network
failure, or -- the common local case -- SUPABASE_URL/
SUPABASE_SERVICE_ROLE_KEY simply not being set) and logs a warning via
`err_console` instead of propagating, so a rails run with no hosted
credentials configured behaves identically to one where Mission Control
doesn't exist at all. `run_id` is `None` whenever `_mc_start_run` failed;
`_mc_step`/`_mc_finish` then no-op immediately rather than attempting a
write with no run to attach it to.

Robustness invariants:
- **PR opens ONLY on a green final gate.** A red gate (initial, exhausted
  retry, or post-revision), an empty branch, a SessionError, a blown budget,
  or a GitHubError all end in a journaled terminal record and a non-PR exit.
- **A green gate's work is never discarded just because nobody ran `git
  commit`.** Headless coding agents (Task 9's first live dogfood run: a real
  212s Claude session, gate green, zero commits) frequently edit files
  without committing them. When the branch has zero commits AND the worktree
  is dirty, the loop auto-commits the session's work itself (see
  `_auto_commit`) before falling through to the existing empty-diff check --
  only a branch with BOTH zero commits AND a clean tree is genuine
  `no_changes`. An agent that already committed its own work is never
  touched by this path.
- A `SessionError` (timeout / spawn / capture failure from an adapter) is
  caught, journaled as outcome="error" with the transcripts collected so far
  plus the failing session's partial transcript, and re-raised as
  `typer.Exit(1)` -- never an unhandled crash.
- A whole-run wall-clock budget (`total_timeout_s`) bounds the entire run: a
  shrinking per-session/-gate timeout is derived from the remaining budget,
  and exhaustion journals outcome="timeout" and exits.
- **The review verdict recorded and published is always the FINAL one.** A
  REQUEST_CHANGES verdict triggers exactly one revision cycle; once that
  revision's gate is green, the reviewer runs ONE more time (read-only, same
  engine) against the REVISED diff, and `review_verdict` is overwritten with
  THAT verdict before the journal row is written or the PR body composed.
  The cycle is bounded -- at most initial review + one revision + one final
  review, never unbounded -- and a PR still opens on a green final gate even
  if the final verdict is still REQUEST_CHANGES (the human merging the PR is
  the actual gate); the PR body says so explicitly in that case rather than
  masquerading the honest verdict as an approval.

Recursion guard: the composed builder prompt tells the agent to run
`just gate` itself (see `rails.prompts.compose`) -- and `just gate`'s pytest
step would otherwise collect `test_real_gate_passes_against_repo_root`, which
calls `run_gate` again. `extra_env` forces `RAILS_REAL_GATE=0` on every
subprocess this loop launches (builder AND reviewer sessions, plus our own
`run_gate_fn` calls) so that test stays skipped inside a rails-driven run.
`DATABASE_URL` rides in the same dict because the agent's in-session
`just gate` (and our own `run_gate_fn`) needs a live Postgres for pytest.

Self-improvement flywheel (the explicit "we also need a self-learning
self-improvement mechanism" requirement): the loop is the hinge between the
two halves.
  - FORWARD channel: `_read_learnings` reads the committed, human-curated
    `rails/LEARNINGS.md` and `compose()` injects it into every builder
    prompt (original, retry, AND revision -- retries/revisions reuse
    `original_prompt` verbatim) so accumulated lessons compound forward into
    every future run.
  - PROPOSAL channel: once a run is about to end in outcome="pr_opened", ONE
    extra read-only "retro" session (`compose_retro`, same builder engine)
    reflects on the run's own diff + final review/gate summary and proposes
    0-3 generalizable lessons (`parse_retro_lessons`; `NONE` or an empty/
    unparseable reply yields zero). Proposals are NEVER auto-written to
    LEARNINGS.md -- they land in a "## Proposed LEARNINGS" section of the PR
    body and in the journal's `proposed_learnings` field, both purely
    advisory; a human curates the actual file by hand when merging (or in a
    follow-up commit). The retro is bounded to exactly one session, skipped
    entirely on `retro=False` (`--no-retro`) or any non-pr_opened outcome,
    and a `SessionError` inside it is caught and swallowed (logged as a
    warning, `proposed_learnings=[]`) rather than failing a run whose PR is
    otherwise ready to open.

Enforced reproduce-then-fix (`enforce_repro=True`, `triage` only -- see
`rails.agents.triage`): turns "reproduce the bug first" from an instruction
the agent may or may not follow into a MACHINE-CHECKED proof, per TDFlow
(EACL 2026), which frames bug-fixing as "obtain a failing reproduction test,
then make it pass" and finds writing that failing test is the actual
bottleneck. When set, two extra phases run inside the SAME worktree BEFORE
the normal build flow:
  - PHASE 1 (RED): `compose_repro` asks the builder for ONLY a new failing
    test, no fix. The session runs, any uncommitted work is auto-committed
    (so the diff genuinely reflects it), then the FULL gate runs and its
    structured `pytest` StepResult (`_pytest_step`) -- never the session's
    own claim -- is inspected. Reproduction requires BOTH: the pytest step
    actually failed, AND the branch's diff genuinely touches a file under
    `tests/` or `web/e2e/` (`_touches_tests`) -- a gate that's red for an
    unrelated reason (a stray lint break) is not proof of anything. Bounded
    to `_MAX_PHASE1_REPRO_ATTEMPTS` tries (one retry, re-prompted to try
    again) before giving up. Failure to reproduce -- most likely the report
    doesn't describe a real bug -- journals outcome="cannot_reproduce" and
    stops: no fix is attempted, no review runs, no PR opens.
  - PHASE 2 (GREEN): once phase 1 proves the bug, `compose_fix` asks the
    builder to fix the non-test code so the SAME reproduction test passes,
    without weakening or deleting it. This becomes `original_prompt` for the
    existing build+retry loop below (reused byte-for-byte -- see the `while`
    loop after this docstring's build step), so it inherits the exact same
    bounded-retry/gate-green semantics `build-feature` already has, then
    falls through to the unmodified review -> PR flow.
`repro_confirmed`/`repro_evidence` on the journaled `RunRecord` are the
proof artifact: `repro_confirmed` is True ONLY once BOTH phases genuinely
succeeded (a phase-2 gate that never turns green -- an ordinary
outcome="gate_failed" -- leaves it False even though phase 1 succeeded,
since the red->green PROOF never completed), and the PR body surfaces the
same proof for the human merging it. `build-feature`/`migrate`/`review`
never set `enforce_repro` (defaults False), so their flow is byte-identical
to before this feature existed.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rails import github, mission_control
from rails.adapters import get_adapter
from rails.adapters.base import SessionError, SessionResult
from rails.config import RailsConfig
from rails.gate import GateResult, StepResult, run_gate
from rails.github import GitHubError
from rails.journal import RunRecord
from rails.journal import record as journal_record
from rails.prompts import (
    compose,
    compose_fix,
    compose_repro,
    compose_retro,
    compose_retry,
    compose_review,
)
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

# Enforced reproduce-then-fix (TDFlow, EACL 2026): phase 1's reproduction
# attempt is bounded to this many tries -- one initial attempt plus exactly
# ONE retry, never unbounded -- before concluding the reported bug could not
# be reproduced (see `run_agent_task`'s `enforce_repro`).
_MAX_PHASE1_REPRO_ATTEMPTS = 2

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


_RETRO_NONE_RE = re.compile(r"^\s*NONE\s*$", re.IGNORECASE)
_RETRO_BULLET_RE = re.compile(r"^(?:[-*]|\d+[.)])\s+(.*)$")
_MAX_RETRO_LESSONS = 3


def parse_retro_lessons(final_message: str, *, max_lessons: int = _MAX_RETRO_LESSONS) -> list[str]:
    """Parse a retro session's `final_message` (see `rails.prompts.compose_retro`)
    into 0-`max_lessons` proposed-lesson strings.

    The literal token `NONE` (any case, whitespace-tolerant, as the entire
    message) -- or an empty/blank message -- means "nothing generalizable
    this run" and yields `[]`, never a fabricated lesson. Lines starting with
    a markdown bullet (`-`, `*`) or a numbered prefix (`1.`, `2)`) have that
    prefix stripped and are taken one-per-line; a reply with NO bullet-style
    lines at all falls back to treating every non-blank line as one lesson
    (so a plain-prose reply still yields something usable instead of
    silently zero). Always capped at `max_lessons`, regardless of how many
    lines the engine produced -- a runaway reply must never balloon the PR
    body or the journal."""
    stripped = final_message.strip()
    if not stripped or _RETRO_NONE_RE.match(stripped):
        return []
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    bulleted = []
    for line in lines:
        match = _RETRO_BULLET_RE.match(line)
        if match:
            bulleted.append(match.group(1).strip())
    lessons = bulleted if bulleted else lines
    return lessons[:max_lessons]


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


def _has_uncommitted_changes(wt_path: Path) -> bool:
    """Whether the worktree has ANY uncommitted content -- staged, unstaged,
    or untracked (`git status --porcelain`, non-empty output). Used only to
    decide whether a zero-commit branch is a genuine `no_changes` (agent did
    nothing at all) or a rescue-worthy `no_changes`-that-isn't (agent left
    real, uncommitted edits behind -- see the auto-commit rescue in
    `run_agent_task`)."""
    result = subprocess.run(
        ["git", "-C", str(wt_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return bool(result.stdout.strip())


def _touches_tests(wt_path: Path, *, base: str = "main") -> bool:
    """Whether the branch's diff since `base` touches anything under
    `tests/` or `web/e2e/` (`git diff --name-only base...HEAD`, mirroring
    `_diff`'s three-dot range). Enforced reproduce-then-fix's phase 1
    (`run_agent_task`'s `enforce_repro`) uses this as its second guard: a
    gate whose `pytest` step happens to be red is NOT proof of a genuine
    reproduction unless the session actually added/changed a test file --
    an unrelated lint break or a stale gate result must never be mistaken
    for "the bug was reproduced"."""
    result = subprocess.run(
        ["git", "-C", str(wt_path), "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    paths = result.stdout.splitlines()
    return any(p.startswith("tests/") or p.startswith("web/e2e/") for p in paths)


def _pytest_step(gate_result: GateResult) -> StepResult | None:
    """The gate's `pytest` StepResult, or `None` if this GateResult's steps
    don't include one. Enforced reproduce-then-fix's phase 1 needs the
    PYTEST step specifically (not the gate's overall `ok`) -- a reproduction
    gate is expected to have pytest red while every other step may still be
    green, so `GateResult.ok` (which is False the moment ANY step fails)
    can't distinguish "the reproduction worked" from "something unrelated
    also broke"."""
    return next((step for step in gate_result.steps if step.name == "pytest"), None)


def _read_learnings(repo_root: Path) -> str | None:
    """Read `<repo_root>/rails/LEARNINGS.md` -- the self-improvement
    flywheel's forward channel (see the module docstring and
    `rails.prompts.compose`'s `learnings` param): a human-curated file
    injected into EVERY future agent prompt. Returns `None` (never raises)
    when the file doesn't exist yet -- a fresh checkout, or a repo that
    hasn't seeded it -- so `compose()` simply omits the section. Not an
    injected collaborator, like `_diff`/`_count_commits`/
    `_has_uncommitted_changes`: tests monkeypatch
    `rails.agents.loop._read_learnings` directly."""
    path = repo_root / "rails" / "LEARNINGS.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _auto_commit(wt_path: Path, *, message: str) -> None:
    """`git add -A && git commit -m message` inside the worktree -- the
    rescue path for a session that made real, gate-passing edits but never
    ran `git commit` itself. Stages EVERYTHING (`-A`), matching what an
    agent's own `git add -A && git commit` would have done."""
    subprocess.run(
        ["git", "-C", str(wt_path), "add", "-A"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(wt_path), "commit", "-m", message],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _mc_start_run(
    *,
    task_kind: str,
    task_summary: str,
    engine: str,
    reviewer_engine: str | None,
    worktree_branch: str,
) -> str | None:
    """Best-effort: create this run's `agent_runs` row on the HOSTED
    Supabase project (see module docstring's "Mission Control telemetry").
    Returns the new run's id, or `None` on ANY failure (missing hosted
    credentials is the common local case) -- callers treat `None` as "no
    Mission Control row exists for this run" and skip every further
    `_mc_step`/`_mc_finish` call rather than erroring."""
    try:
        return mission_control.start_run(
            {
                "task_kind": task_kind,
                "task_summary": task_summary,
                "engine": engine,
                "reviewer_engine": reviewer_engine,
                "worktree_branch": worktree_branch,
                "status": "running",
            }
        )
    except Exception as exc:  # noqa: BLE001 -- observability must never break a run
        err_console.print(
            "[yellow]warning: mission control start_run failed (non-fatal -- this run "
            f"continues unaffected, just invisible to /mission-control): {exc}[/yellow]"
        )
        return None


def _mc_step(
    run_id: str | None, seq: int, phase: str, status: str, detail: str | None = None
) -> None:
    """Best-effort `run_steps` row. A no-op when `run_id` is `None` (this
    run's `agent_runs` row was never created -- see `_mc_start_run`)."""
    if run_id is None:
        return
    try:
        mission_control.add_step(run_id, seq, phase, status, detail)
    except Exception as exc:  # noqa: BLE001 -- observability must never break a run
        err_console.print(
            f"[yellow]warning: mission control add_step failed (non-fatal): {exc}[/yellow]"
        )


def _mc_finish(
    run_id: str | None,
    *,
    status: str,
    gate_ok: bool | None,
    review_verdict: str | None,
    cost_usd: float | None,
    pr_url: str | None,
    retries: int,
) -> None:
    """Best-effort terminal PATCH of this run's `agent_runs` row. A no-op
    when `run_id` is `None` (see `_mc_start_run`)."""
    if run_id is None:
        return
    try:
        mission_control.finish_run(
            run_id,
            status=status,
            gate_ok=gate_ok,
            review_verdict=review_verdict,
            cost_usd=cost_usd,
            pr_url=pr_url,
            retries=retries,
        )
    except Exception as exc:  # noqa: BLE001 -- observability must never break a run
        err_console.print(
            f"[yellow]warning: mission control finish_run failed (non-fatal): {exc}[/yellow]"
        )


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


def _compose_repro_retry(repro_prompt: str) -> str:
    """Enforced reproduce-then-fix's ONE bounded phase-1 retry (see the
    module docstring): the previous attempt didn't actually reproduce the
    bug -- either its test passed against current code, or it made no
    test-file change at all. Reuses the phase-1 framing verbatim (mirrors
    `compose_retry`'s "recompose from the ORIGINAL, not nested" discipline)
    plus one instruction sentence; never nests across more than this single
    retry since phase 1 is capped at `_MAX_PHASE1_REPRO_ATTEMPTS` tries."""
    return f"""{repro_prompt}

---

Your test passed -- it doesn't reproduce the bug (or you made no test change
at all). Write ONE test that actually FAILS against the current code,
demonstrating the reported bug. Do not change any non-test code.
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
    retro: bool = True,
    enforce_repro: bool = False,
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

    `retro` (self-improvement flywheel, default True): when the run is about
    to end in outcome="pr_opened", run exactly ONE extra, read-only "retro"
    session (same builder engine) that proposes 0-3 generalizable lessons
    for `rails/LEARNINGS.md` -- see the module docstring's "Self-improvement
    flywheel" section for the full contract (never auto-written, bounded to
    one session, failure-isolated). `--no-retro` on `build-feature` threads
    through to this flag.

    `enforce_repro` (default False; `triage` passes True -- see the module
    docstring's "Enforced reproduce-then-fix" section): inserts a bounded
    reproduce-then-fix protocol BEFORE the normal build flow, requiring the
    gate's own `pytest` step to genuinely fail (proving the bug) before any
    fix is attempted, then requiring the full gate to genuinely pass
    (proving the fix) -- both machine-checked, never trusted claims. A
    failure to reproduce ends the run as outcome="cannot_reproduce" (no fix,
    no review, no PR). `build-feature`/`migrate`/`review` never set this.
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
    proposed_learnings: list[str] = []
    # Enforced reproduce-then-fix (enforce_repro=True; see module docstring):
    # both start False/None and are promoted only once phase 2's gate is
    # confirmed green -- every terminal outcome journaled before that point
    # (including "cannot_reproduce" itself, and an ordinary "gate_failed" if
    # phase 2 never turns green) keeps repro_confirmed=False, since the
    # red->green PROOF never actually completed.
    repro_confirmed = False
    repro_evidence: str | None = None

    with worktree_cm(slug_from(title), repo_root=cfg.repo_root) as wt:
        transcript_dir = wt.path / ".rails-transcripts"
        mc_seq = count(1)
        run_id = _mc_start_run(
            task_kind=task_kind,
            task_summary=title,
            engine=engine,
            reviewer_engine=reviewer_engine,
            worktree_branch=wt.branch,
        )

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
                proposed_learnings=proposed_learnings,
                repro_confirmed=repro_confirmed,
                repro_evidence=repro_evidence,
            )
            _mc_finish(
                run_id,
                status=outcome,
                gate_ok=gate_ok,
                review_verdict=review_verdict,
                cost_usd=run.cost_usd,
                pr_url=pr_url,
                retries=retries,
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
            _mc_step(run_id, next(mc_seq), label, "started")
            try:
                session = adapter.run(prompt, cwd=wt.path, timeout_s=timeout_s, extra_env=extra_env)
            except SessionError as exc:
                failing = getattr(exc, "transcript_path", None)
                _phase(f"[bold red]✗ {label} failed: {exc}[/bold red]")
                if failing is not None:
                    _phase(f"inspect the failing session transcript: {failing}")
                _mc_step(run_id, next(mc_seq), label, "failed", str(exc))
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
            _mc_step(
                run_id,
                next(mc_seq),
                label,
                "ok" if session.ok else "failed",
                session.final_message[:500] if session.final_message else None,
            )
            return session

        def _gate(gate_ok_before: bool) -> GateResult:
            timeout_s = _budget_or_exit(gate_ok_before)
            _phase("▶ gate …")
            result = run_gate_fn(wt.path, env=extra_env, total_timeout_s=timeout_s)
            if result.ok:
                _phase("[green]✓ gate green[/green]")
                _mc_step(run_id, next(mc_seq), "gate", "ok")
            else:
                failed = ", ".join(step.name for step in result.failed_steps())
                _phase(f"[bold red]✗ gate red -- failing steps: {failed}[/bold red]")
                _mc_step(run_id, next(mc_seq), "gate", "failed", failed)
            return result

        def _run_retro(prompt: str) -> list[str]:
            """The self-improvement flywheel's per-run retro session: ONE
            extra, read-only session on the SAME builder engine (never the
            reviewer), asking it to propose 0-3 generalizable lessons (see
            `rails.prompts.compose_retro`). Bounded and failure-isolated by
            design -- called ONLY from the pr_opened path (never on a failed
            outcome), and a `SessionError` here is caught and swallowed
            (logged as a warning, empty proposal) rather than raised: the
            run's PR is otherwise ready to open and must not be sacrificed
            over a reflection step. Budget-exhaustion is handled the same
            way (a hard `_budget_or_exit` would itself raise and abort the
            run, which is exactly what must NOT happen here)."""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _phase("[yellow]⚠ retro skipped: no run budget remaining[/yellow]")
                _mc_step(
                    run_id, next(mc_seq), "retro", "failed", "skipped: no run budget remaining"
                )
                return []
            timeout_s = max(1, min(300, int(remaining)))
            # A FRESH readonly adapter for the builder's engine -- distinct
            # from `builder` (constructed readonly=False) even though it may
            # be the very same underlying CLI, mirroring how the reviewer is
            # always constructed readonly (I3): a reflection session must
            # never mutate the worktree either.
            retro_adapter = make_adapter(engine, cfg, readonly=True)
            _phase(f"▶ retro session ({retro_adapter.name}) …")
            _mc_step(run_id, next(mc_seq), "retro", "started")
            try:
                session = retro_adapter.run(
                    prompt, cwd=wt.path, timeout_s=timeout_s, extra_env=extra_env
                )
            except SessionError as exc:
                _phase(
                    f"[yellow]⚠ retro session failed -- no proposed learnings this run "
                    f"(the PR is already ready to open regardless): {exc}[/yellow]"
                )
                _mc_step(run_id, next(mc_seq), "retro", "failed", str(exc))
                return []
            sessions.append(session)  # cost_usd still sums across ALL sessions, incl. retro
            lessons = parse_retro_lessons(session.final_message)
            _phase(
                f"retro proposed {len(lessons)} lesson(s) for rails/LEARNINGS.md "
                "(human review required before adding them)"
            )
            _mc_step(run_id, next(mc_seq), "retro", "ok", f"{len(lessons)} lesson(s) proposed")
            return lessons

        _phase(f"worktree ready: branch [bold]{wt.branch}[/bold] at {wt.path}")
        _phase(f"transcripts under {transcript_dir} (tail -f to follow the live session)")
        _mc_step(run_id, next(mc_seq), "worktree", "ok", f"branch {wt.branch} at {wt.path}")

        # Self-improvement flywheel, forward channel: inject the committed,
        # human-curated rails/LEARNINGS.md (if any) into the ORIGINAL prompt
        # -- retries/revisions below recompose FROM this same original_prompt
        # (see M1), so accumulated lessons ride along into every session of
        # this run without being re-read or re-injected each time.
        learnings = _read_learnings(cfg.repo_root)

        # --- enforced reproduce-then-fix, PHASE 1 (RED) -- see the module
        # docstring's "Enforced reproduce-then-fix" section. Runs BEFORE the
        # normal build step below, in the SAME worktree, only when the
        # caller (`triage`) opted in via `enforce_repro=True`.
        if enforce_repro:
            _phase("▶ phase 1: reproduce (assert failing test) …")
            repro_prompt = compose_repro(
                task_kind, task_body, engine_label=f"{engine} (rails)", learnings=learnings
            )
            reproduced = False
            pytest_step: StepResult | None = None
            added_test = False
            for attempt in range(1, _MAX_PHASE1_REPRO_ATTEMPTS + 1):
                label = (
                    "reproduction session" if attempt == 1 else f"reproduction retry {attempt - 1}"
                )
                sessions.append(_run_session(builder, repro_prompt, label=label, gate_ok=False))
                # Auto-commit BEFORE the gate/diff checks below (unlike the
                # build flow's own rescue, which only runs once the gate is
                # ALREADY green): both the pytest-red check and
                # `_touches_tests` need the new test file to actually be
                # part of the branch's committed diff, and a headless
                # session frequently forgets to commit at all (the same Task
                # 9 dogfood failure mode, just guarded proactively here).
                if _has_uncommitted_changes(wt.path):
                    _phase("▶ auto-committing uncommitted phase-1 session work")
                    _auto_commit(
                        wt.path,
                        message=(
                            f"test: reproduce -- {title}\n\n"
                            f"Failing reproduction test written by the {engine} builder "
                            "session (phase 1 of the enforced reproduce-then-fix protocol) "
                            "and auto-committed by nextlane-rails because the session left "
                            f"it uncommitted.\n\nCo-Authored-By: {engine} via nextlane-rails "
                            "<noreply@nextlane.dev>"
                        ),
                    )
                    _mc_step(
                        run_id,
                        next(mc_seq),
                        "commit",
                        "ok",
                        "auto-committed phase-1 reproduction test",
                    )
                phase1_gate = _gate(False)
                pytest_step = _pytest_step(phase1_gate)
                added_test = _touches_tests(wt.path)
                reproduced = pytest_step is not None and not pytest_step.ok and added_test
                if reproduced or attempt >= _MAX_PHASE1_REPRO_ATTEMPTS:
                    break
                _phase(
                    "[yellow]retrying phase 1 (bounded to one retry) -- the reported bug "
                    "was not reproduced yet[/yellow]"
                )
                repro_prompt = _compose_repro_retry(repro_prompt)

            if not reproduced:
                if pytest_step is not None and pytest_step.ok:
                    message = (
                        "the reproduction test passed without any fix — could not "
                        "reproduce the reported bug; not opening a PR"
                    )
                elif not added_test:
                    message = (
                        "phase 1 made no test-file change under tests/ or web/e2e/ — "
                        "could not reproduce the reported bug; not opening a PR"
                    )
                else:
                    message = (
                        "the gate's pytest step could not be evaluated — could not "
                        "reproduce the reported bug; not opening a PR"
                    )
                _phase(f"[bold yellow]✗ {message}[/bold yellow]")
                _record(gate_ok=False, pr_url=None, outcome="cannot_reproduce")
                raise typer.Exit(1)

            _phase(f"[green]✓ bug reproduced — pytest red[/green] (attempt {attempt})")
            _phase("▶ phase 2: fix (assert green) …")
            original_prompt = compose_fix(
                task_kind, task_body, engine_label=f"{engine} (rails)", learnings=learnings
            )
        else:
            original_prompt = compose(
                task_kind, task_body, engine_label=f"{engine} (rails)", learnings=learnings
            )

        # --- build (PHASE 2 "fix" when enforce_repro; the ordinary, only
        # build step otherwise) ---
        sessions.append(
            _run_session(
                builder,
                original_prompt,
                label="fix session" if enforce_repro else "builder session",
                gate_ok=False,
            )
        )
        gate_result = _gate(False)

        # --- retries: recompose from the ORIGINAL prompt each time + the
        # LATEST gate summary (M1) -- NOT from the previous retry prompt,
        # which would accumulate stale gate tails and duplicate instructions.
        # Unmodified by enforce_repro: phase 2 reuses this exact bounded
        # retry loop (the "must be GREEN" half of the reproduce-then-fix
        # proof), just seeded from compose_fix's prompt instead of compose's.
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

        if enforce_repro:
            # The full red->green PROOF: phase 1 above already confirmed the
            # gate's pytest step failed (RED); the retry loop just above
            # confirmed the FULL gate is now green (GREEN). Both halves
            # machine-checked -- never a trusted claim from either session.
            repro_confirmed = True
            repro_evidence = (
                f"pytest RED at phase 1 (confirmed on attempt {attempt} of "
                f"{_MAX_PHASE1_REPRO_ATTEMPTS}), GREEN after fix (phase 2: {retries} "
                "retry attempt(s))"
            )
            _phase(f"[green]✓ enforced reproduce-then-fix confirmed:[/green] {repro_evidence}")

        # --- auto-commit rescue: a green gate proves the session's edits are
        # valid, but headless coding agents frequently forget to `git
        # commit` even though they left real work in the worktree (the Task
        # 9 dogfood bug this guards against). Triggers when the branch truly
        # has zero commits AND there is uncommitted content to rescue -- OR,
        # for enforce_repro, unconditionally on any leftover dirty state:
        # phase 1 above already committed the reproduction test, so
        # `_count_commits == 0` would never be true again by the time phase
        # 2 finishes even if phase 2's OWN fix session forgot to commit (the
        # exact same dogfood failure mode, just on the run's second session
        # instead of its first). An agent that already committed its own
        # work and left the tree clean is untouched either way
        # (`_has_uncommitted_changes` is False, short-circuiting this whole
        # check regardless of `enforce_repro`).
        if (_count_commits(wt.path) == 0 or enforce_repro) and _has_uncommitted_changes(wt.path):
            _phase("▶ auto-committing uncommitted session work (agent left it uncommitted)")
            commit_message = (
                f"{title}\n\n"
                f"Work produced by the {engine} builder session and auto-committed by "
                "nextlane-rails because the session left it uncommitted.\n\n"
                f"Co-Authored-By: {engine} via nextlane-rails <noreply@nextlane.dev>"
            )
            _auto_commit(wt.path, message=commit_message)
            _mc_step(
                run_id, next(mc_seq), "commit", "ok", "auto-committed uncommitted session work"
            )

        # --- I2: empty-diff guard. A green gate on a branch with STILL zero
        # commits (even after the auto-commit rescue above) means the agent
        # truly did nothing worth reviewing/PRing.
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
        _mc_step(run_id, next(mc_seq), "review-verdict", "ok", review_verdict)

        revised = False
        if review_verdict != "APPROVE":
            revised = True
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
            # Honesty fix (audit bug 1): the revision is re-reviewed EXACTLY
            # ONCE more (read-only, same reviewer engine) against the REVISED
            # diff. This keeps the cycle bounded -- at most initial review +
            # one revision + one final review, never an unbounded loop -- while
            # making sure `review_verdict` (and everything derived from it:
            # the journal row, the PR body) reflects what the reviewer
            # actually thinks of the FIXED code, not the stale, pre-revision
            # REQUEST_CHANGES. The final verdict is recorded as-is even if
            # it's still REQUEST_CHANGES -- see the PR body composition below,
            # which then says so explicitly instead of masquerading.
            final_diff = _diff(wt.path)
            final_review_session = _run_session(
                reviewer,
                compose_review(final_diff, checklist=CHECKLIST),
                label="final review",
                gate_ok=True,
            )
            sessions.append(final_review_session)
            review_verdict = parse_verdict(final_review_session.final_message)
            _phase(f"final review verdict from {reviewer_engine}: [bold]{review_verdict}[/bold]")
            _mc_step(run_id, next(mc_seq), "review-verdict", "ok", f"final: {review_verdict}")

        if not open_pr:
            run = _record(gate_ok=True, pr_url=None, outcome="completed_no_pr")
            _phase(
                "open_pr=False -- leaving the worktree AND branch in place for inspection "
                f"({wt.path})"
            )
            _print_summary(run)
            return run

        # --- self-improvement flywheel: per-run retro (PROPOSAL channel) ---
        # Only reached on the path that WILL open a PR (every failed outcome
        # above already raised/returned) -- exactly the "outcome pr_opened"
        # condition the spec requires. `retro=False` (--no-retro) skips it
        # entirely; nothing here ever writes rails/LEARNINGS.md itself.
        if retro:
            final_review_message = (
                final_review_session.final_message if revised else review_session.final_message
            )
            retro_diff = final_diff if revised else diff
            retro_review_summary = (
                f"Final review verdict: {review_verdict}\n\n{final_review_message}"
            )
            retro_prompt = compose_retro(task_body, retro_diff, retro_review_summary)
            proposed_learnings = _run_retro(retro_prompt)
        else:
            _phase("retro skipped (--no-retro)")

        summary_text = sessions[0].final_message or title
        if not revised:
            journal_note = f"Cross-vendor review by {reviewer_engine} (rails): {review_verdict}"
        elif review_verdict == "APPROVE":
            journal_note = (
                f"Final cross-vendor review by {reviewer_engine} (rails): APPROVE "
                "(after one revision)."
            )
        else:
            # Honesty fix (audit bug 1): a still-REQUEST_CHANGES final verdict
            # must be stated plainly, not masqueraded -- the PR still opens
            # (a green final gate + a human merge gate are the actual
            # invariants), but the body flags it for human attention.
            journal_note = (
                f"Final cross-vendor review by {reviewer_engine} (rails): REQUEST_CHANGES "
                "(after one revision) -- human review advised."
            )
        body = github.pr_body(
            summary_text,
            engine_label=f"{engine} (rails)",
            journal_note=journal_note,
        )
        if repro_confirmed:
            # The proof, surfaced for the human merging this PR (see the
            # module docstring's "Enforced reproduce-then-fix" section):
            # both halves of the red->green cycle were machine-checked via
            # the gate's own structured pytest result, never a trusted claim
            # from either builder session.
            body = (
                f"{body}\n\n"
                "✓ **Enforced reproduce-then-fix**: a reproduction test failed before the "
                "fix (bug confirmed) and passes after (fix verified).\n"
                f"{repro_evidence}"
            )
        if proposed_learnings:
            # Human-gated: PROPOSE only, in the PR body a reviewer reads --
            # never auto-write rails/LEARNINGS.md itself.
            lessons_md = "\n".join(f"- {lesson}" for lesson in proposed_learnings)
            body = (
                f"{body}\n\n"
                "## Proposed LEARNINGS (human-gated — review before adding to "
                "rails/LEARNINGS.md)\n"
                f"{lessons_md}"
            )
        _phase("▶ opening PR …")
        _mc_step(run_id, next(mc_seq), "pr", "started")
        try:
            pr_url = open_pr_fn(worktree=wt, title=title, body=body, repo_root=cfg.repo_root)
        except GitHubError as exc:
            # Best-effort only: we can't cheaply tell whether the push landed
            # before gh failed, so we don't attempt remote branch cleanup here
            # (a TODO, not a correctness bug -- see module docstring/spec I5b).
            _phase(f"[bold red]✗ opening the PR failed: {exc}[/bold red]")
            _mc_step(run_id, next(mc_seq), "pr", "failed", str(exc))
            _record(gate_ok=True, pr_url=None, outcome="error")
            raise
        _phase(f"[green]✓ PR opened:[/green] {pr_url}")
        _mc_step(run_id, next(mc_seq), "pr", "ok", pr_url)

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
