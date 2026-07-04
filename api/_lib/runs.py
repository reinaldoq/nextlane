"""Mission Control READ path: `GET /api/runs` and `GET /api/runs/{id}`.

Spec ref: Mission Control -- the one sanctioned post-Phase-1-freeze app
addition (design spec Sec6/Sec12), a live in-app dashboard at
`/mission-control` that polls these two endpoints. `agent_runs`/`run_steps`
(see `supabase/migrations/<ts>_mission_control.sql`) are WRITTEN by the
rails runner directly against the hosted project via PostgREST
(`rails/mission_control.py`, service-role key) -- there is deliberately no
POST/PATCH route here. This router is READ-ONLY, over the pooled
`DATABASE_URL` exactly like every other module (`api/_lib/vehicles.py`):
the superuser pooled connection bypasses RLS the same way it does for
vehicles, so no policy is needed on these deny-by-default tables.
"""

import uuid

from fastapi import APIRouter, Depends, Query

from .auth import current_user
from .db import pool
from .errors import api_error

router = APIRouter(dependencies=[Depends(current_user)])


@router.get("/runs")
def list_runs(limit: int = Query(default=50, ge=1, le=200)):
    """Most recent `limit` agent_runs, newest first (ts_iso desc, id desc
    tiebreaker for stability). `total` is the full table count, not just
    this page -- there is no offset/pagination here (Mission Control shows
    a recent-runs feed, not a browsable archive)."""
    sql = (
        "SELECT *, count(*) over() as total FROM agent_runs ORDER BY ts_iso desc, id desc LIMIT %s"
    )
    with pool().connection() as conn:
        rows = conn.execute(sql, [limit]).fetchall()
        if rows:
            total = rows[0]["total"]
        else:
            total = conn.execute("SELECT count(*) AS total FROM agent_runs").fetchone()["total"]

    items = [{k: v for k, v in row.items() if k != "total"} for row in rows]
    return {"items": items, "total": total}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID):
    """One agent_runs row plus its run_steps, ordered oldest-first (`seq
    asc`) so the web timeline renders top-to-bottom in the order the run
    actually progressed."""
    with pool().connection() as conn:
        run = conn.execute("SELECT * FROM agent_runs WHERE id = %s", [run_id]).fetchone()
        if run is None:
            raise api_error(404, "not_found", "run not found")
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = %s ORDER BY seq asc", [run_id]
        ).fetchall()
    return {**run, "steps": steps}
