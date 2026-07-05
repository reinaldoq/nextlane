# api/AGENTS.md — backend (FastAPI on Vercel)

Rules for changing anything under `api/`. The repo-root `AGENTS.md` has the
cross-cutting rules; this file is the backend depth. Reference module:
`api/_lib/vehicles.py` — copy its shape, don't improvise a new one.

## The module pattern (to add a module `X`, e.g. `parts`)

1. **Migration** — see [`supabase/AGENTS.md`](../supabase/AGENTS.md) (RLS
   deny-by-default, check constraints, indexes, `updated_at` trigger).
2. **Router** — `api/_lib/X.py`:
   `APIRouter(dependencies=[Depends(current_user)])`, `XIn`/`XPatch` Pydantic
   models (`XPatch` → `model_config = ConfigDict(extra="forbid")` and excludes
   any status/lifecycle field), parameterized SQL throughout, a sort whitelist
   for the list endpoint, `RETURNING *` on every write, and
   `Depends(rate_limited(..., scope="writes"))` on every write route.
3. **Wire it in** — `api/index.py`:
   `app.include_router(X_router, prefix="/api")`.
4. **Web page** — see [`web/AGENTS.md`](../web/AGENTS.md).
5. **Tests** — see "Tests" below.

The `.claude/skills/scaffold-module/SKILL.md` skill automates this exact
procedure with concrete code skeletons for each step.

## Backend conventions

- **Auth is router-level.** Every business router declares
  `dependencies=[Depends(current_user)]`; rate limiting is additive on writes
  only, never the sole carrier of auth. `/api/health` is the only
  unauthenticated route. Internal operator-only routes (Mission Control,
  `/api/runs*`) use `dependencies=[Depends(require_operator)]` — 403 for
  non-operators; `OPERATOR_EMAILS` (comma-separated) is the allowlist, surfaced
  to the client via `GET /api/me`.
- **Money is integer cents** (`bigint` in Postgres), never floats — all the way
  from DB through the API and TS types. Only the UI layer divides/×100.
- **All errors go through `api_error(status, code, message, details, headers)`**
  (`api/_lib/errors.py`), which flattens to a top-level `{code, message,
  details}` JSON body the web client branches on.
- **SQL is always parameterized.** User-controlled identifiers (sort/filter
  columns) never get string-interpolated — they resolve through a whitelist
  set/dict first (`SORT_COLUMNS` in `vehicles.py`); an unmatched value is a 422,
  not a query.
- **Status/lifecycle changes go through a dedicated `POST /{id}/status`**,
  guarded by the transition matrix (`api/_lib/transitions.py`) evaluated under
  `SELECT ... FOR UPDATE` in one transaction. Status is excluded from PATCH
  entirely (a PATCH carrying `status` is a 422 via `extra="forbid"`).
- **Rate-limit write endpoints** via `rate_limited(limit, scope=...)`
  (`api/_lib/ratelimit.py`); reads stay unlimited.
- **CORS is intentionally absent** — the SPA and API are one same-origin Vercel
  project. If origins ever split, add CORS middleware deliberately; don't add it
  "just in case."
- **Dependencies & secrets.** Run `just pin-api` after any Python dependency
  change (regenerates `api/requirements.txt`); keep `api/.python-version` synced
  with the root `pyproject.toml`. Only `.env.example` is committed; the Supabase
  service-role key never reaches Vercel, CI, or the browser.

## Tests

One file per module, `tests/test_X_api.py`, built from the shared `db_client` /
`auth_headers` / `clean` fixture trio (`tests/conftest.py`) plus a 2-line
module-local `_clean` autouse wrapper (see the top of
`tests/test_vehicles_api.py`), a body-factory helper, parametrized 422 cases,
and a single "401 without token on every route" test. Model this file directly
on `tests/test_vehicles_api.py`.
