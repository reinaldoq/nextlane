# web/AGENTS.md — frontend (Vite + React 19 + Ant Design)

Rules for changing anything under `web/`. The repo-root `AGENTS.md` has the
cross-cutting rules; this file is the frontend depth.

## Stack pins that bite

- **Ant Design v6, NOT v5.** Use current v6 API (`theme.useToken()`,
  `Card variant="borderless"` — not v5's `bordered={false}`, `menu={{ items }}`
  on `Dropdown`, etc.). Don't paste v5-era snippets.
- **oxlint** is the linter (`npm run lint`), not ESLint. `no-floating-promises`
  is enforced — `void` a promise you intentionally don't await.
- **npm@10-shaped lockfile.** GitHub's Node 24 ships npm 11, but CI and Vercel
  both pin npm@10 for installs. After any dependency change, regenerate with
  `npx --yes npm@10 install --prefix web` — an npm-11-shaped lockfile fails CI.
- **Don't add/upgrade npm deps unless the task requires it** — worktrees share
  `web/node_modules` with the main checkout.

## The page pattern

A module's UI is an Ant Design page under `web/src/pages/`, routed inside
`<AuthGuard>` in `web/src/App.tsx` (see how `InventoryPage` nests under `/`).
Feature/form components live in `web/src/components/` (e.g. `VehicleFormDrawer`,
`RowActions`). Every server call goes through the typed wrapper
`web/src/lib/api.ts` (`api.get/post/del`, `ApiError`) — never a raw `fetch`.

## Client conventions

- **Money is integer cents** everywhere; only this UI layer divides/×100 for
  display and input.
- **PATCH sends only the changed fields** (the server is `extra="forbid"`).
- **Client mirrors of server-enforced rules** — the status transition matrix in
  `RowActions.tsx`, sort whitelists — carry a `// keep in sync with <file>`
  comment. The server is always the enforcer of record; the client copy is only
  a UX shortcut.
- `ApiError` (`web/src/lib/api.ts`) consumes the `{code, message, details}`
  envelope for code-based branching and field-level 409 surfacing (e.g.
  duplicate VIN on a form field).
- Supabase auth token `storageKey` is `nextlane-auth`.
- **Row actions** live behind a per-row `⋯` overflow menu (`RowActions`,
  aria-label "Row actions"); status changes + delete confirm through a dialog.
- **Mission Control** (`/mission-control`) is an internal operator console, not a
  dealer screen: the nav link + route render only when `GET /api/me` reports
  `is_operator` (fails closed while loading / on error).

## Tests

Playwright e2e (`web/e2e/`, `npm run e2e`) is the local/CI golden-path gate;
`npm run e2e:prod` is the pre-submission smoke against the live URL. When you
change a UI affordance the golden path drives (row actions, the form, report
issue), update `web/e2e/smoke.spec.ts` (and `prod-smoke.spec.ts`) to match.
