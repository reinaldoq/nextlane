# rails/AGENTS.md — the day-2 agent runner

Rules for changing or understanding `rails/` — the vendor-agnostic runner that
drives headless claude/codex/gemini sessions through one shared loop:
worktree → build → gate → cross-vendor review → PR. The repo-root `AGENTS.md`
has the cross-cutting rules.

## Structure

- `rails/adapters/{claude,codex,gemini}.py` — one `AgentSession` adapter each
  (`build_argv` + transcript parse), every parser grounded in a real captured
  transcript (`tests/rails/fixtures/`). A fake engine drives CI, so no real CLI
  is needed to run the suite.
- `rails/agents/loop.py` — the shared `run_agent_task` loop and the reviewer
  `CHECKLIST`. `build_feature.py` / `triage.py` / `migrate.py` / `review.py` are
  thin wrappers that compose a task body and call the loop.
- `rails/prompts.py` — per-task prompt composition (`compose`, `compose_repro`,
  `compose_fix`, `compose_review`, `compose_retro`); `wrap_untrusted` is the
  prompt-injection boundary.
- `rails/gate.py`, `rails/worktree.py`, `rails/journal.py`, `rails/github.py`,
  `rails/mission_control.py`, `rails/config.py`, `rails/doctor.py`.

## Loop invariants (don't weaken these)

A PR opens **only on a green final gate** AND after an independent, read-only,
cross-vendor review; the loop never merges — a human does. Every terminal path
is journaled to `rails/journal/runs.jsonl`. Runs are isolated in `.worktrees/`.
A review verdict that fails to parse fails safe to `REQUEST_CHANGES`, never a
silent approve.

## Security boundary

`rails/config.py`'s `allowed_env()` passes a strict whitelist to every agent
subprocess (never `os.environ` wholesale): base vars + only the four `GIT_*`
identity vars. `GIT_SSH_COMMAND` / `GIT_ASKPASS` / `GIT_DIR` and every secret
(LLM keys, service-role) are never forwarded. Untrusted input is wrapped by
`wrap_untrusted` and is data, never instructions.

## Self-improvement flywheel

`rails/LEARNINGS.md` is a committed, **human-curated** file injected into every
builder prompt automatically. After a PR opens, one read-only "retro" session
proposes 0–3 generalizable lessons (PR body "## Proposed LEARNINGS" + the
journal's `proposed_learnings`) — PROPOSALS ONLY, never auto-written to
`LEARNINGS.md`. `--no-retro` skips it.

## Enforced reproduce-then-fix (`triage`)

Phase 1: a session writes a test the gate RUNS and confirms genuinely FAILS
against current code (a machine-checked reproduction, not a trusted claim) —
bounded to one retry, else `cannot_reproduce` (no fix, review, or PR). Phase 2:
fix until the full gate is green, keeping the reproduction test; the phase-1
test file must SURVIVE the phase-2 diff or `repro_confirmed` stays false.
Grounded in TDFlow (EACL 2026) — see
[`docs/design-rationale.md`](../docs/design-rationale.md).

## Engine flags & budget

`--engine claude|codex|gemini` (builder), `--reviewer <engine>` (defaults to the
*other* of claude/codex; gemini → claude), `--no-pr` stops before the PR. Budget
asymmetry, documented not a bug: `claude` carries a hard `--max-budget-usd` cap;
`codex`/`gemini` expose no dollar-budget flag, so their blast radius is bounded
only by the run's wall-clock timeout and their sandbox/approval mode. Adapters:
claude `--setting-sources project,local` (+ `--permission-mode`, read-only for
reviewers), codex `--ignore-user-config` + read-only sandbox for reviewers,
gemini best-effort. Tests live in `tests/rails/` (fake-engine); real-engine
tests are gated behind `RAILS_REAL_ENGINE`.
