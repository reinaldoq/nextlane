# Live-session demo script

Run-of-show for the interview panel session: the panel hands us a fresh,
unseen task and we build it live with the rails, on top of a repo where the
rails have already shipped five real cross-vendor runs to production. This
doc is the presenter's cheat sheet — preflight, timings, exact commands,
fallbacks, and the talking points that turn "it ran" into "here's why it's
trustworthy."

**Live app:** https://nextlane-blond.vercel.app (Vercel git auto-deploy is
connected: merge to `main` → prod, no manual deploy step).

**Mission Control:** https://nextlane-blond.vercel.app/mission-control — the
live in-app dashboard of agent runs. Keep this open on a second screen/tab;
it updates *while a run happens* (a `running` row with a growing step
timeline), so it's the visual anchor for the whole demo.

## Preflight (do this 10-15 minutes before the call, not during it)

Run the one command that replaces this whole checklist:

```bash
uv run rails doctor
```

`rails doctor` prints one `PASS`/`FAIL` line per check and exits `0` only if
every **critical** check passes (an optional engine missing — currently only
`gemini` — is reported but never fails the run):

| check | what it verifies |
| --- | --- |
| `env-file` | `.env` exists at the repo root |
| `env-keys` | `DATABASE_URL`, `SUPABASE_JWKS_URL` (gate), `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (triage) all resolve |
| `postgres` | `DATABASE_URL` is reachable, `SELECT 1` runs |
| `migrations` | the `vehicles` table exists (proxy for "migrations applied") |
| `engine:claude` / `engine:codex` | on `PATH` — **critical**, the demo's default builder/reviewer pair |
| `engine:gemini` | on `PATH` — informational only, best-effort support |
| `gh-auth` | `gh auth status` exits 0 |

If it's not all green, fix in this order (this is what each check is really
asking for):

1. **Docker/Postgres down** → `supabase start` (spins up local Postgres +
   Auth + PostgREST), then `just seed` (`supabase db reset` — applies
   migrations + seed data). Re-run `rails doctor`; `postgres` and
   `migrations` should both flip to `PASS`.
2. **An engine missing** → install/log into that CLI (`claude`, `codex`,
   `gemini`); `rails doctor`'s `engine:*` lines resolve via `shutil.which`,
   so once it's on `PATH` the check goes green with no repo changes needed.
3. **`gh-auth` failing** → `gh auth login`.
4. **`env-file`/`env-keys` failing** → copy `.env.example` to `.env` and
   fill in the local Supabase values from `supabase start`'s own output
   (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`) plus `DATABASE_URL`/
   `SUPABASE_JWKS_URL`. `rails` auto-loads `.env` on every invocation — no
   `just` wrapper required.

Also confirm, once, before going live: the deployed app loads and you can
log in (see step (a) below — this is also step 1 of the actual demo, so
doing it in preflight doubles as a rehearsal). **Log in with an operator
account** (the demo `reviewer@nextlane-demo.dev` is on `OPERATOR_EMAILS`): a
"Nextlane staff" badge appears and Mission Control is visible. A non-operator
(dealer) login sees neither — the demo's visual anchor would be invisible.

## Narrative arc

Total budget: ~20-25 minutes end-to-end, built around **two** real agent
runs (one prepared, one from the panel) at 3-8 minutes of actual session
time each — that's what the five already-merged runs actually took
(PR#18: 255s / PR#19: 463s / PR#23: 206s / PR#26: 273s / PR#39: 261s, per
`rails/journal/runs.jsonl`). Everything else is narration over dead air,
watching Mission Control fill in, or fast to click through.

| time | segment | what you do | what you say |
| --- | --- | --- | --- |
| 0:00-0:30 | **(a) The deployed app** | Open https://nextlane-blond.vercel.app, log in | "This is a real DMS slice — vehicle inventory, Postgres-backed, RLS-enforced — deployed on Vercel. Everything you'll see get built in the next 20 minutes ships to this exact URL." |
| 0:30-2:30 | **(b) The four artifacts** | Open, in order: `AGENTS.md` (agent rules), `.claude/skills/scaffold-module` + `.claude/skills/domain-reviewer` (skills), `rails/agents/` + `uv run rails --help` (the day-2 agents), `.github/workflows/ci.yml` + branch protection settings (the gate/CI) | "Four graded artifacts: the rules every engine reads before touching code, two reusable skills, the day-2 agents themselves — one Typer CLI, one shared loop — and the deterministic gate + CI that nothing reaches `main` without passing." |
| 2:30-3:30 | **(c) Proof it already ran** | Open **Mission Control** (`/mission-control`) — five runs with engine badges + verdict chips; click one to show its step timeline. Optionally `cat rails/journal/runs.jsonl \| jq .` for the raw evidence. | "Five real cross-vendor runs, already merged, already live: #18 Claude built a stats endpoint, Codex reviewed; #19 Codex built a UI feature + e2e, Claude reviewed; #23 a dealer-filed bug went through `rails triage` end to end; #26 Claude built the CSV export; #39 Claude added the inventory total-value aggregate. This dashboard is *inside the product* — the agents' work, shown in the product they're building. Nothing here is staged." |
| 3:30-10:00 | **(d) One prepared day-2 task, live** | See "Prepared task" below. Run it; **switch to the Mission Control tab and watch the `running` row + step timeline appear live** while you narrate the phase banners; `tail -f` the transcript during dead air; land on the gate + review + proposed lesson + PR. | See narration script below. |
| 10:00-11:00 | **Merge → auto-deploy** | `gh pr checks --watch`, squash-merge, then reload the prod URL to show the new behavior live | "Merge to `main`, Vercel's git integration takes it from here — no manual deploy step. Refreshing prod now." |
| 11:00-20:00+ | **(f) The panel's fresh task** | Same command shape, a spec nobody has seen before | "Same command, unseen spec — this is the same loop you just watched, not a rehearsed one." |

### (d) Prepared task — narration script

Pick something small and safely scoped ahead of time (shape it like PR#18/
#19: one endpoint or one UI affordance on the `vehicles` module, not a new
module). **First, `uv run rails runs` and skim `api/_lib/vehicles.py` to pick
something genuinely UNbuilt** — a task that already exists makes the agent
no-op live. Already built, so don't reuse: `/api/vehicles/stats` and its
`total_value_cents` field (#18/#39), `GET /api/vehicles/export.csv` (#26),
the "Clear filters" toolbar (#19). A good fresh default:

```bash
uv run rails build-feature \
  "Add year_min and year_max query params to GET /api/vehicles that filter \
   the list to vehicles within that inclusive year range, following the \
   existing q/status filter + sort-whitelist pattern, with an API test" \
  --engine claude --reviewer codex
```

Why `claude` as the default builder: it's the one engine with a **hard
`--max-budget-usd` spend cap** (`rails/adapters/claude.py`, configured via
`RAILS_MAX_BUDGET_USD`, default $2.00) enforced by the CLI itself, and it's
consistently the fastest of the three in the journal so far. `codex` and
`gemini` expose no dollar-budget flag — their blast radius is bounded only
by the run's timeout and sandbox/approval mode, a documented asymmetry, not
a bug (see AGENTS.md, "Budget discipline").

As it runs, narrate the timestamped phase banners as they print (they're
literally designed to fill this dead air —
`rails/agents/loop.py`'s module docstring calls a real run "10-30 minutes of
otherwise-dead air"):

- `worktree ready: branch <slug> at .worktrees/<slug>` — "Isolated git
  worktree, not the main checkout — the agent can't step on anything else in
  flight."
- `▶ build-feature (claude) …` — "Headless Claude Code session, driven from
  a spec + the vehicles module as the reference pattern."
- In a second terminal: `tail -f .worktrees/<slug>/.rails-transcripts/*.jsonl`
  — "Every turn is a JSONL line on disk — this is what 'not a black box'
  looks like."
- `▶ gate …` / `✓ gate green` — "Same six steps as `just gate`: ruff check,
  ruff format, pytest, web lint, web typecheck, web build. All six, every
  time, no early exit — an agent retrying a red gate gets the whole picture,
  not one problem per retry."
- `review verdict from codex: APPROVE` — "Independent, read-only,
  cross-vendor review of the full diff — Codex reviewing Claude's branch. A
  verdict that doesn't parse cleanly fails safe to `REQUEST_CHANGES`, never
  a silent approve."
- `▶ opening PR …` / `✓ PR opened: <url>` — "PR opens only because the gate
  is green AND an independent reviewer approved. It never merges itself."

Then, on screen:

1. Open the PR — point out the review verdict in the body and the
   **"Proposed LEARNINGS"** section (the self-improvement flywheel's retro
   session proposing 0-3 new lessons for review — never auto-applied).
2. `gh pr checks --watch` — CI re-running the same gate independently.
3. Squash-merge, `git checkout main && git pull`.
4. Reload the prod URL — the new behavior is live.

## The killer demo (highlight this one)

If time allows only one thing to really land, make it this: the **full
report → fix → ship loop**, closed end to end, no human writing code at any
step:

1. Click **"Report issue"** in the live app (`web/src/components/
   ReportIssueModal.tsx`) and file a bug.
2. `uv run rails triage --event <app_events id>` — fetches the reported row,
   then runs the **enforced red→green protocol**: phase 1, the agent writes
   *only* a reproduction test and the harness **runs it and confirms it
   FAILS** (bug genuinely reproduced — not the agent's word for it); phase 2,
   the agent fixes the code and the harness confirms that test now **PASSES**
   with no regressions. If the "repro" passes green with no fix — e.g. the
   two seeded reports about a photo-upload feature and a price-range filter
   that *don't exist* — triage honestly reports **`cannot_reproduce`** and
   opens no PR, instead of hallucinating a fix.
3. Cross-vendor review, gate, PR — same loop as build-feature.
4. Point at the PR body's **"✓ Enforced reproduce-then-fix"** proof line and
   the proposed lesson, merge, watch Vercel auto-deploy.

> **Talking point for this step:** this is the single most evidence-backed
> reliability lever in the whole system (see `docs/design-rationale.md`).
> TDFlow (EACL 2026) shows a mandatory failing-reproduction-test gate takes
> SWE-Bench bug-fixing to 93–94%, and its own finding is that *writing the
> failing test is the bottleneck, not the fix* — which is exactly why the
> harness verifies the red state mechanically rather than trusting it.

**This already happened for real — PR#23.** A dealer-shaped report ("our
inventory integration's sort request gets an error that doesn't list the
valid fields") went through exactly this loop: Claude reproduced it with two
new failing tests (`test_list_sort_error_lists_allowed_fields`,
`test_list_sort_error_flags_bad_direction` in `tests/test_vehicles_api.py`),
fixed `api/_lib/vehicles.py::_parse_sort` to enumerate `allowed_fields`/
`allowed_directions` in the 422's machine-readable `details`, Codex reviewed
`APPROVE`, the retro proposed a real generalizable lesson, a human merged
it, and Vercel auto-deployed it — **the fix is live right now**: while
logged in, an authenticated `GET /api/vehicles?sort=color:asc` against prod
returns a 422 whose `details` now lists `allowed_fields`/
`allowed_directions` (every list/business route requires auth — see
AGENTS.md convention #1). The proposed lesson from that run's retro is now
curated into
`rails/LEARNINGS.md` (see below) — show that file too as the closed loop's
last mile: a human decided it was genuinely generalizable and folded it in.

If live time is short, **narrate #23 from the journal + the merged PR
instead of re-running it** — it's real, it's proven, and re-triggering a
fresh triage run live risks colliding with whatever the panel's fresh task
touches.

## Fallbacks / risk table

| risk | mitigation |
| --- | --- |
| A live session is running long | `--no-pr` stops the loop short of opening a PR, leaving the worktree + branch under `.worktrees/` for inspection — narrate what's there instead of waiting it out. |
| The gate goes red | The loop retries with the failing steps fed back into the next prompt (bounded retries, full per-step output — not just the first failure). Narrate the retry banner live; it's the same resilience story either way. |
| Unsure which engine to run live | Default to `claude`: it has the hard `--max-budget-usd` cap (bounded spend, not just bounded time) and has been the fastest engine in the journal so far. |
| Docker/Postgres won't come up | The deployed app + `rails/journal/runs.jsonl` are enough to demo the whole story without a single local command — the live URL already reflects five real merged runs. |
| A live run misbehaves or the network flakes | Fall back to `rails/journal/runs.jsonl` and the five merged PRs (#18, #19, #23, #26, #39) as static, already-verified proof — every field (engine, reviewer, verdict, cost, PR URL) is right there, no live run required to make the point. |
| Something looks wrong in the diff mid-review | That's the review step doing its job — narrate it as evidence the cross-vendor review is real, not theater: a verdict that doesn't parse fails safe to `REQUEST_CHANGES`, and a genuine `REQUEST_CHANGES` is a legitimate, tell-able outcome. |

## Talking points

- **No API keys.** Every session runs on a subscription coding-agent CLI
  (`claude`, `codex`, `gemini`) — `rails/config.py`'s `allowed_env()` passes
  a strict whitelist (`PATH`, `HOME`, `SHELL`, `TERM`, `LANG`, `LC_ALL`,
  `USER`, `TMPDIR`, plus the four `GIT_*` identity vars) to every
  agent-session subprocess, never `os.environ` wholesale — so there's no
  API key to leak in the first place, and no accidental secret forwarding
  either.
- **Vendor-agnostic, both directions proven.** PR#18: Claude built, Codex
  reviewed. PR#19: Codex built, Claude reviewed. It's not "Claude with a
  Codex-shaped rubber stamp" — both engines have played both roles for real.
- **The security boundary is a real allowlist, not a convention.** The same
  `allowed_env()` whitelist explicitly excludes the `GIT_*` namespace except
  four identity vars — `GIT_SSH_COMMAND`, `GIT_ASKPASS`, `GIT_DIR` etc. are
  command-execution or isolation-escape hooks and are never forwarded to a
  session subprocess, on purpose.
- **PR-only-on-green, honest final verdict.** The loop opens a PR only after
  a green gate AND an independent cross-vendor review; a review verdict that
  fails to parse fails safe to `REQUEST_CHANGES`, never a silent approve —
  and the loop reports its own honest final verdict rather than
  overwriting a `REQUEST_CHANGES` with a happier-looking summary.
- **The self-improvement flywheel is human-gated, not automatic.** A retro
  session may only *propose* lessons (in the PR body's "Proposed
  LEARNINGS" section and the journal's `proposed_learnings` field); nothing
  is ever auto-written to `rails/LEARNINGS.md`. A human decides what's
  genuinely generalizable — PR#23's proposed lesson about surfacing
  whitelist values in 422 `details` is the one example of that curation
  step actually happening (see `rails/LEARNINGS.md`).
- **Enforced red→green isn't a prompt, it's a gate.** Triage doesn't just
  *ask* the agent to write a failing test first — the harness runs the test
  and checks the gate's own `pytest` step result to confirm it's actually
  red before allowing a fix, and confirms green after. A "fix" that never
  had a failing test, or a bug that can't be reproduced, can't produce a PR.
- **Mission Control is observation, not enforcement — on purpose.** The
  dashboard and journal are how we *see* what happened; they don't decide
  whether a run ships. That's a deliberate split (see
  `docs/design-rationale.md`): enforcement is the gate + the review + the
  red→green protocol; observability is evidence. It's why tests can never
  write to the dashboard (the isolation fix), and why a dead dashboard would
  never let a bad change through.
- **Every design choice is grounded, not improvised.** `docs/design-
  rationale.md` maps each decision — deterministic harness, blocking gate,
  single-writer + isolated diff-only review, enforced red→green,
  observability-not-enforcement — to the primary sources (Anthropic, OpenAI,
  Cognition/Devin, TDFlow, JetBrains). If the panel asks "why did you build
  it this way," the answer is cited.
