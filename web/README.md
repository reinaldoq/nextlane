# web

[![CI](https://github.com/reinaldoq/nextlane/actions/workflows/ci.yml/badge.svg)](https://github.com/reinaldoq/nextlane/actions/workflows/ci.yml)

Nextlane DMS web frontend — Vite + React + TypeScript + Ant Design.

## Commands

- `npm run dev` — dev server (proxies `/api` to `http://127.0.0.1:8000`)
- `npm run build` — typecheck + production build to `dist/`
- `npm run typecheck` — `tsc -b --noEmit`
- `npm run lint` — oxlint (type-aware)
- `npm run preview` — serve the production build locally
- `npm run e2e` — Playwright smoke test (real API + Postgres; see `e2e/`)

Stop local dev servers (ports 8000/5173) before running `npm run e2e` — the
e2e config refuses to reuse running servers so it always tests the production
build.

## Environment

Vite reads env vars only from `web/`: put `VITE_*` values in `web/.env.local`
(gitignored). See the root `.env.example` for the documented list.
