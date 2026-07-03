#!/usr/bin/env bash
set -euo pipefail
# Creates (or upserts) an email+password user via the GoTrue admin API.
# usage: SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... ./scripts/create_user.sh email password
curl -sf -X POST "$SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$1\",\"password\":\"$2\",\"email_confirm\":true}"
echo
