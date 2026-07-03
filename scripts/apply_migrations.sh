#!/usr/bin/env bash
set -euo pipefail
for f in supabase/migrations/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
if [ "${APPLY_SEED:-}" = "1" ]; then
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/seed.sql
fi
