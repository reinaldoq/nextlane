# LEARNINGS

This file is the self-improvement flywheel's committed, human-curated
memory. It is **human-gated**: a rails agent run may only *propose* new
entries (as a "## Proposed LEARNINGS" section in its PR body — see
`rails.agents.loop.run_agent_task`'s per-run retro); nothing is ever
appended here automatically. A human reviews a proposal in the PR, decides
whether it's genuinely generalizable, and — if so — edits this file
themselves as part of merging (or a follow-up commit).

Every lesson below is injected into **every future rails agent prompt**
(`rails.prompts.compose`, via `rails.agents.loop._read_learnings`), so the
whole point of curating this file tightly is that the rails get measurably
smarter run-over-run: each dogfood run either confirms the existing rules
still hold or, occasionally, earns a new one.

Keep entries concise, generalizable, and actionable — a rule the NEXT agent
can actually apply, not a changelog of what happened. One line per lesson,
plus a one-line "why".

## Lessons

- **Register literal sub-routes before parameterized ones.** A `GET
  /vehicles/stats` route must be declared before `GET /vehicles/{id}`, or
  FastAPI parses `stats` as an `{id}` and the specific route is shadowed.
  (Surfaced when the stats endpoint was added — a test now guards it.)

- **Commit your work before finishing.** The loop auto-commits
  gate-passing changes as a safety net, but an uncommitted session nearly
  caused valid work to be discarded as "no changes". Make focused
  conventional commits yourself.

- **When consolidating multiple requests into one endpoint** (e.g.
  StatCards' 3 count calls → 1 `/stats` call), preserve the existing UI
  contract exactly — caption text, token colors, loading/empty states — so
  the refactor is invisible to users.

- **When raising a 422 for a whitelist-validated parameter, surface the
  allowed values in the machine-readable `details` object** (not just the
  human message) — external integrations parse `details` to self-correct.
  (Proven in the triage of the invalid-sort bug, PR#23.)
