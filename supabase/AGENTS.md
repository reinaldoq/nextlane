# supabase/AGENTS.md — migrations & schema

Rules for anything under `supabase/`. Postgres is the source of truth; `api/`
talks to it directly via `psycopg` (the app does not use PostgREST). Migrations
are timestamped SQL in `supabase/migrations/`.

## Migration conventions

- **Filename**: create with `supabase migration new <slug>` (timestamped). CI
  applies migrations in filename order via `scripts/apply_migrations.sh`, so the
  timestamp prefix matters.
- **RLS `enable` with no policies (deny-by-default)** on every table. The app's
  pooled superuser connection bypasses RLS exactly like it does for `vehicles`
  and `agent_runs` — there is no per-row policy to add; the API is the access
  path.
- **Check constraints** for every invariant you can express in SQL, and
  **indexes** for anything you'll filter or sort by.
- **`updated_at` trigger** via `set_updated_at()` with `set search_path = ''` on
  the trigger function — see
  `20260703152303_init_inventory.sql` for the exact pattern.
- **Money columns are `bigint`** (integer cents).
- Enum-like text columns use a `check (col in (...))` constraint; when you add a
  new allowed value, update the constraint in a new migration (see
  `20260704160000_mc_status_outcomes.sql`).

## Applying

- Local: `just seed` (`supabase db reset` — applies every migration + seed).
- Hosted: `supabase db push`.

Migrations are the one part of `supabase/` a `rails migrate` run edits; the
rest of the app freeze still applies (see the root `AGENTS.md`).
