# Deployment

One Vercel project (`nextlane`) serves both halves of the app:

- **Static SPA** — `web/` built by Vite to `web/dist` (`installCommand`/`buildCommand` in `vercel.json`).
- **Python API** — `api/index.py` exposes the FastAPI `app` as a single Vercel
  function. `api/_lib/` modules are importable by the function but are **not**
  exposed as routes (verified: they return 404). All traffic to `/api/*` is
  rewritten to the function; everything else falls back to the SPA.

Production: https://nextlane-blond.vercel.app

## Deploy

```bash
vercel deploy          # preview
vercel deploy --prod   # production
```

## Facts learned the hard way (keep these true)

- **Lockfile must be npm-10-shaped.** Vercel's build image runs npm 10, which
  rejects lockfiles written by npm 11 (`npm ci` fails with
  `Missing: @emnapi/core ... from lock file`). After changing web deps,
  regenerate with `npx npm@10 install --prefix web` and verify
  `npx npm@10 ci --prefix web` passes before deploying.
- **Python version pinning**: the function builder does NOT read the repo-root
  `.python-version`; it reads `api/.python-version` (committed, `3.12`).
  Keep it in sync with `pyproject.toml`'s `requires-python`.
- **`api/requirements.txt` is generated, not hand-edited.** It is the fully
  pinned dependency closure for the deployed function, exported from
  `uv.lock` so production runs exactly what tests ran against. Regenerate
  with `just pin-api` after dependency changes.
- **Preview deployments are SSO-protected** (HTTP 302 → vercel.com/sso-api).
  Automated checks against previews need a Protection Bypass token — or
  target production, which is public.
- **Security headers** (CSP, X-Frame-Options, etc.) are set in `vercel.json`
  and apply to every route. CSP `connect-src` allows only self + Supabase.
