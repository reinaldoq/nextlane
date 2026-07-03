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
supabase start          # local Postgres + Auth + PostgREST
just seed                # supabase db reset (applies migrations + seed.sql)
just dev-api              # FastAPI on :8000
just dev-web               # Vite dev server on :5173 (separate shell)
```

Copy `.env.example` for the env vars each half expects — `web/` reads Vite
vars from `web/.env.local` (gitignored); `api/` reads process env directly.

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

Phase 2 — day-2 agent tooling lives in `rails/` (coming).
