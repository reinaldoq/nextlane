# Nextlane DMS

[![CI](https://github.com/reinaldoq/nextlane/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/reinaldoq/nextlane/actions/workflows/ci.yml)

A case-study slice of a Dealer Management System: vehicle inventory. Create,
search, filter, transition (available → reserved → sold) and delete vehicles
against a real Postgres-backed API, with row-level-security-enforced access
and an in-app issue-reporting loop.

**Live:** https://nextlane-blond.vercel.app

## Stack

React 19 + Vite + antd (web) · FastAPI on a single Vercel Python function
(api) · Postgres via Supabase, RLS deny-by-default (db) · Playwright (e2e) ·
uv + pytest (api tests) · GitHub Actions (CI, branch-protected `main`).

## Quickstart

Prereqs: [uv](https://docs.astral.sh/uv/), Node (see `.nvmrc`), Docker,
[Supabase CLI](https://supabase.com/docs/guides/cli).

```bash
supabase start   # local Postgres + Auth + PostgREST
just seed        # supabase db reset (applies migrations + seed.sql)
just dev-api     # FastAPI on :8000
just dev-web     # Vite dev server on :5173 (separate shell)
```

Copy `.env.example` for the env vars each half expects — `web/` reads Vite
vars from `web/.env.local` (gitignored); `api/` reads process env directly.

### Local auth caveat

Local `supabase start` issues HS256 tokens, which the ES256-only API rejects
by design. For a working local login, point `web/.env.local`'s VITE vars and
the API's `SUPABASE_JWKS_URL`/`SUPABASE_JWT_ISSUER` at the hosted Supabase
project instead (hybrid mode: hosted auth + local DB), then create a user
with `scripts/create_user.sh`. The [deployed URL](https://nextlane-blond.vercel.app)
is the fully-working reference environment.

## Tests

```bash
just test    # pytest against a local Postgres
just e2e     # Playwright golden-path smoke (boots api + a production web build)
just gate    # lint + test + web build — what CI runs on every PR
```

`web/e2e/prod-smoke.spec.ts` is a separate, opt-in spec that drives a real
browser against the live production deployment and hosted database (real
login, no session injection). It's skipped unless `PROD_URL` is set:
`npm --prefix web run e2e:prod`.

## Deployment

See [`docs/deployment.md`](docs/deployment.md) for how the single Vercel
project serves both the SPA and the API function, and the deploy gotchas
worth knowing before touching it.

## AI rails

`rails/` is a vendor-agnostic **day-2 agent runner**: Claude, Codex, or
Gemini can extend this app through the exact same protected gate a human
uses — a PR against branch-protected `main`, lint + tests + e2e all green.
Sessions run on subscription coding-agent CLIs (`claude`, `codex`, `gemini`),
never an API key.

**Four graded artifacts:** agent rules ([`AGENTS.md`](AGENTS.md),
[`CLAUDE.md`](CLAUDE.md)); reusable skills
([`.claude/skills/scaffold-module`](.claude/skills/scaffold-module),
[`.claude/skills/domain-reviewer`](.claude/skills/domain-reviewer)); the
day-2 agents themselves (`uv run rails build-feature|triage|migrate|review`,
one Typer CLI over one shared loop in `rails/agents/loop.py`); and the
eval/verification loop — the deterministic gate (`uv run rails gate`, a
structured mirror of `just gate`) plus GitHub Actions CI plus branch
protection, so nothing an agent produces reaches `main` unverified.

```bash
uv run rails engines                                   # list engines available on PATH
uv run rails build-feature "add a stats endpoint..." --engine claude --reviewer codex
uv run rails triage --event <app_events id> --engine codex
uv run rails migrate "add a discount_cents column..." --engine gemini
uv run rails review --pr 42 --engine claude --comment
```

`build-feature` implements a feature end-to-end from a plain-language spec;
`triage` turns a reported `app_events` row into a reproduction test + fix —
**enforced**: the harness's own gate must confirm the reproduction test
genuinely fails before any fix is attempted, and genuinely passes after (see
[`docs/design-rationale.md`](docs/design-rationale.md));
`migrate` authors and applies a schema change; `review` runs a standalone
cross-vendor review against an open PR (or `--range`), independent of the
other three. `--engine` picks the builder (defaults to `RAILS_ENGINE`);
`--reviewer` picks the cross-vendor reviewer (defaults to the *other* of
claude/codex; gemini defaults to claude); `--no-pr` runs the full loop but
stops short of opening a PR, leaving the worktree under `.worktrees/` for
inspection. **The loop opens a PR only on a green gate and never
self-merges** — a human always merges.

Each run: an isolated **git worktree** → a headless builder **session** →
the deterministic **gate** (bounded retries on red) → an independent,
read-only **cross-vendor review** of the full branch diff (a verdict that
doesn't parse fails safe to `REQUEST_CHANGES`, never a silent approve) →
**PR** only once the gate is green. It's observable (timestamped phase
banners as it runs, a tailable transcript per session under
`.worktrees/<slug>/.rails-transcripts/`), interruptible, and every run — PR
opened, gate failed, review rejected, or errored — is journaled to
[`rails/journal/runs.jsonl`](rails/journal/runs.jsonl).

**Self-improvement flywheel:** the rails also learn, run over run. Every
prompt is seeded with [`rails/LEARNINGS.md`](rails/LEARNINGS.md), a small,
committed, **human-curated** file of accumulated lessons
(`rails.prompts.compose`'s `learnings` section). After a PR opens, one
extra, read-only **retro** session reflects on that run's own diff and
review and proposes 0-3 new, generalizable lessons — never auto-applied.
They land in the PR's "Proposed LEARNINGS" section and in the journal
(`proposed_learnings`) purely as a suggestion; a human decides whether to
fold one into `rails/LEARNINGS.md` when merging. `--no-retro` skips it.

**Proof it's real:** three cross-vendor dogfood runs have merged through this
exact loop —
[#18](https://github.com/reinaldoq/nextlane/pull/18) (Claude built a
`GET /api/vehicles/stats` endpoint + updated web StatCards to one request;
Codex reviewed → `APPROVE`),
[#19](https://github.com/reinaldoq/nextlane/pull/19) (Codex built a "Clear
filters" toolbar button + Playwright e2e; Claude reviewed → `APPROVE`), and
[#23](https://github.com/reinaldoq/nextlane/pull/23) (Claude triaged and
fixed a reported inventory-integration bug; Codex reviewed → `APPROVE`), all
real headless sessions on subscription CLIs with no API keys — see their
entries (engine, reviewer, verdict, cost) in `rails/journal/runs.jsonl`, or
run `uv run rails runs` for a pretty-printed table of the same data.

**Mission Control:** a live in-app dashboard of these runs at
[`/mission-control`](https://nextlane-blond.vercel.app/mission-control)
inside the DMS itself — engine badges, status/verdict chips, cost, PR links,
and a per-run step timeline, polling `GET /api/runs`/`GET /api/runs/{id}`
every few seconds (deliberately polling, not Realtime — the API is the read
path like every other table; the rails runner writes to it directly via
`rails/mission_control.py`).

| engine | notes |
| --- | --- |
| `claude` | hard `--max-budget-usd` cap; per-session USD cost reported |
| `codex` | no dollar-budget flag (token counts only); bounded by timeout + sandbox mode |
| `gemini` | best-effort support; no dollar-budget flag either |

Run `uv run rails engines` to see which are actually on `PATH` for you.
Before a live session, `uv run rails doctor` runs a preflight instead: local
Postgres reachable, each engine on `PATH`, `gh auth status`, `.env` keys,
and migrations applied — see [`docs/demo-script.md`](docs/demo-script.md).
