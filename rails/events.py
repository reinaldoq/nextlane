"""Supabase `app_events` access for the triage agent: fetch new events and
mark them once handled.

Spec ref: Phase-2 Task 8. Uses the service-role key so PostgREST bypasses RLS
(app_events has no policies -- deny-by-default -- so an anon/authenticated
key could never read it; the service role is the one caller allowed to see
every row). stdlib `urllib` only -- no new dependency.

**Credentials**: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are read from
the environment. Locally these come from the repo's `.env` (gitignored,
populated from `supabase start`/the hosted project's dashboard -- see
`.env.example`'s "scripts only, NEVER deployed, NEVER in CI" note). This
module is NEVER exercised with real credentials in CI: `tests/rails/
test_events.py` injects a fake `opener` for every call, so no network I/O
and no real key are ever required to run the test suite.

Determinism/testability: `opener` (default `urllib.request.urlopen`) is the
sole network seam, injected on both `fetch_new_events` and `mark_event` --
tests pass a fake that records the `urllib.request.Request` it received and
returns canned bytes, exactly like build_feature's adapter/gate/worktree
injection pattern in `rails.agents.loop`.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

_Opener = Callable[[urllib.request.Request], object]


class EventsError(RuntimeError):
    """Raised when required Supabase configuration is missing."""


@dataclass(frozen=True)
class AppEvent:
    """One row of `app_events` (see supabase/migrations for the schema:
    id uuid, kind bug_report|client_error, message text, context jsonb,
    status new|triaged|resolved, created_at timestamptz)."""

    id: str
    kind: str
    message: str
    context: dict
    status: str
    created_at: str


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EventsError(
            f"{name} is not set. Populate it in the repo's local .env (never committed, "
            "never set in CI) -- see .env.example."
        )
    return value


def _base_url() -> str:
    return _env("SUPABASE_URL").rstrip("/")


def _headers() -> dict[str, str]:
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _row_to_event(row: dict) -> AppEvent:
    return AppEvent(
        id=row["id"],
        kind=row["kind"],
        message=row["message"],
        context=row.get("context") or {},
        status=row["status"],
        created_at=row["created_at"],
    )


def fetch_new_events(
    *, limit: int = 10, opener: _Opener = urllib.request.urlopen
) -> list[AppEvent]:
    """GET the `limit` most recent `status=new` app_events, newest first.

    Raises `EventsError` if `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` are
    unset. `opener` is injected for tests (real default: `urllib.request.
    urlopen`) -- it receives the built `Request` and must return a context
    manager whose `.read()` gives the response body bytes.
    """
    url = f"{_base_url()}/rest/v1/app_events?status=eq.new&order=created_at.desc&limit={limit}"
    request = urllib.request.Request(url, headers=_headers(), method="GET")
    with opener(request) as response:
        body = response.read()
    rows = json.loads(body or b"[]")
    return [_row_to_event(row) for row in rows]


def mark_event(event_id: str, status: str, *, opener: _Opener = urllib.request.urlopen) -> None:
    """PATCH `app_events` row `event_id` to `status` (`Prefer: return=minimal`
    -- we don't need the updated row back, just confirmation the write
    landed). Same env/injection contract as `fetch_new_events`."""
    url = f"{_base_url()}/rest/v1/app_events?id=eq.{event_id}"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=minimal"
    payload = json.dumps({"status": status}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    with opener(request) as response:
        response.read()
