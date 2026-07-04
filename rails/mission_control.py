"""Mission Control write path: best-effort agent-run telemetry sent to the
HOSTED Supabase project's `agent_runs` / `run_steps` tables (see
`supabase/migrations/<ts>_mission_control.sql`).

Spec ref: Mission Control -- the one sanctioned post-Phase-1-freeze app
addition (design spec Sec6/Sec12), a live in-app dashboard at
`/mission-control` reading these tables through `api/_lib/runs.py` (pooled
DATABASE_URL, same read path as every other table). This module is the
WRITE side: `rails/agents/loop.py` calls it at run start, at each
phase-banner point, and at run end.

Deliberately the SAME shape as `rails.events` (the existing PostgREST
service-role template): stdlib `urllib` only, `opener` (default
`urllib.request.urlopen`) is the sole network seam injected on every call,
`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` come from the environment (the
runner runs LOCALLY but must write to the DEPLOYED app's hosted DB -- rails
is never pointed at local Supabase for this). Never exercised with real
credentials in CI: `tests/rails/test_mission_control.py` injects a fake
`opener` for every call.

Tests never touch the real network here even when a caller (like
`rails.agents.loop`) relies on the `opener` DEFAULT rather than passing its
own: `tests/rails/conftest.py` has an autouse fixture that neutralizes that
default for every test under `tests/rails/`, regardless of environment.

CRITICAL: this module intentionally RAISES `MissionControlError` (missing
env) same as it lets an opener's network failure propagate -- it does NOT
swallow anything itself. Best-effort/non-fatal behavior is the CALLER's
responsibility (`rails.agents.loop`'s `_mc_start_run` / `_mc_step` /
`_mc_finish` wrappers catch every exception from here and log a warning) so
that Mission Control -- pure observability -- can never break a run.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime
from typing import Callable

_Opener = Callable[[urllib.request.Request], object]


class MissionControlError(RuntimeError):
    """Raised when required Supabase configuration is missing."""


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissionControlError(
            f"{name} is not set. Populate it in the repo's local .env (never committed, "
            "never set in CI) -- see .env.example."
        )
    return value


def _base_url() -> str:
    return _env("SUPABASE_URL").rstrip("/")


def _headers() -> dict[str, str]:
    key = _env("SUPABASE_SERVICE_ROLE_KEY")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def start_run(record: dict, *, opener: _Opener = urllib.request.urlopen) -> str:
    """POST one row to `agent_runs` and return its generated `id`.

    `record` carries whatever columns the caller wants to set at creation
    time (task_kind, task_summary, engine, reviewer_engine, worktree_branch,
    status, ...) -- passed through verbatim as the JSON body. `Prefer:
    return=representation` so the inserted row (and its `id`) comes back in
    the response.
    """
    url = f"{_base_url()}/rest/v1/agent_runs"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    payload = json.dumps(record).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with opener(request) as response:
        body = response.read()
    rows = json.loads(body or b"[]")
    return rows[0]["id"]


def add_step(
    run_id: str,
    seq: int,
    phase: str,
    status: str,
    detail: str | None = None,
    *,
    opener: _Opener = urllib.request.urlopen,
) -> None:
    """POST one row to `run_steps` for `run_id`. `Prefer: return=minimal` --
    the caller only needs confirmation the write landed, never the row back.
    """
    url = f"{_base_url()}/rest/v1/run_steps"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=minimal"
    payload = json.dumps(
        {"run_id": run_id, "seq": seq, "phase": phase, "status": status, "detail": detail}
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with opener(request) as response:
        response.read()


def finish_run(
    run_id: str,
    *,
    status: str,
    gate_ok: bool | None,
    review_verdict: str | None,
    cost_usd: float | None,
    pr_url: str | None,
    retries: int | None = None,
    opener: _Opener = urllib.request.urlopen,
    now_fn: Callable[[], str] = _utc_now_iso,
) -> None:
    """PATCH `agent_runs?id=eq.<run_id>` with the run's terminal fields --
    the loop's `outcome`, whether the final gate was green, the final
    cross-vendor review verdict (if any), the summed session cost, and the
    PR url (if one opened). `retries` is only included in the PATCH body
    when explicitly given (an int, possibly 0) -- omitted entirely
    otherwise, leaving the column untouched. `finished_at` is always set,
    stamped via the injected `now_fn` (default: real UTC clock) so tests can
    supply a deterministic clock, mirroring `rails.agents.loop`'s own
    `now_fn` injection.
    """
    url = f"{_base_url()}/rest/v1/agent_runs?id=eq.{run_id}"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=minimal"
    body: dict = {
        "status": status,
        "gate_ok": gate_ok,
        "review_verdict": review_verdict,
        "cost_usd": cost_usd,
        "pr_url": pr_url,
        "finished_at": now_fn(),
    }
    if retries is not None:
        body["retries"] = retries
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    with opener(request) as response:
        response.read()
