# CLAUDE.md

Read `AGENTS.md` first — it is the canonical source of truth for this repo's
conventions, gate, and agent rules, and it points to nested area guides
(`api/AGENTS.md`, `web/AGENTS.md`, `supabase/AGENTS.md`, `rails/AGENTS.md`) —
read the one for the code you're changing. Everything below is Claude-specific
on top of it.

- Reusable skills live in `.claude/skills/`: `scaffold-module` (generate a
  new DMS module end-to-end) and `domain-reviewer` (review a diff against
  the Nextlane conventions). Use them instead of improvising the same
  procedure from scratch.
- Prefer the `just` recipes (`just gate`, `just test`, `just lint`,
  `just dev-api`, `just dev-web`, `just seed`) over ad hoc commands.
- Follow TDD: write the failing test first, then the implementation.
- The `vehicles` module (`api/_lib/vehicles.py`,
  `web/src/pages/InventoryPage.tsx`, `tests/test_vehicles_api.py`) is the
  reference implementation — copy its shape for anything new.
- Rails-driven sessions run with `--setting-sources project,local` — no
  personal `~/.claude` settings bleed into an agent-run session.
