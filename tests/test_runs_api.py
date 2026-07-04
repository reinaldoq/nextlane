"""Tests for api/_lib/runs.py: the Mission Control READ path.

Spec ref: Mission Control (the one sanctioned post-Phase-1-freeze app
addition, design spec Sec6/Sec12). `agent_runs`/`run_steps` are WRITTEN by
the rails runner directly against the hosted project via PostgREST
(`rails/mission_control.py`, service-role key) -- there is deliberately no
POST route here. This module is READ-ONLY: `GET /api/runs` (recent runs,
newest first) and `GET /api/runs/{id}` (one run + its ordered steps), both
over the pooled `DATABASE_URL` exactly like every other table (RLS
deny-by-default; the superuser pooled connection bypasses it, same as
vehicles). Tests insert rows directly via psycopg (mirroring
`_insert_vehicles_with_identical_created_at` in test_vehicles_api.py) since
there's no API write path to exercise.
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(clean):
    yield


def run_body(**overrides) -> dict:
    body = {
        "ts_iso": None,  # None -> let the column default (now()) apply
        "task_kind": "feature",
        "task_summary": "add a widget",
        "engine": "claude",
        "reviewer_engine": "codex",
        "status": "running",
        "gate_ok": None,
        "retries": 0,
        "review_verdict": None,
        "cost_usd": None,
        "pr_url": None,
        "worktree_branch": "rails/add-a-widget-abc123",
    }
    body.update(overrides)
    return body


def insert_run(**overrides) -> str:
    body = run_body(**overrides)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if body["ts_iso"] is None:
            row = conn.execute(
                "insert into agent_runs "
                "(task_kind, task_summary, engine, reviewer_engine, status, gate_ok, retries, "
                "review_verdict, cost_usd, pr_url, worktree_branch) "
                "values (%(task_kind)s, %(task_summary)s, %(engine)s, %(reviewer_engine)s, "
                "%(status)s, %(gate_ok)s, %(retries)s, %(review_verdict)s, %(cost_usd)s, "
                "%(pr_url)s, %(worktree_branch)s) returning id",
                body,
            ).fetchone()
        else:
            row = conn.execute(
                "insert into agent_runs "
                "(ts_iso, task_kind, task_summary, engine, reviewer_engine, status, gate_ok, "
                "retries, review_verdict, cost_usd, pr_url, worktree_branch) "
                "values (%(ts_iso)s, %(task_kind)s, %(task_summary)s, %(engine)s, "
                "%(reviewer_engine)s, %(status)s, %(gate_ok)s, %(retries)s, %(review_verdict)s, "
                "%(cost_usd)s, %(pr_url)s, %(worktree_branch)s) returning id",
                body,
            ).fetchone()
    return str(row[0])


def insert_step(run_id: str, seq: int, phase: str, status: str = "ok", detail: str | None = None):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "insert into run_steps (run_id, seq, phase, status, detail) values (%s, %s, %s, %s, %s)",
            [run_id, seq, phase, status, detail],
        )


# ---------------------------------------------------------------------------
# 401 without token, on every route
# ---------------------------------------------------------------------------


def test_401_without_token_on_every_route(db_client: TestClient):
    run_id = "00000000-0000-0000-0000-000000000000"
    requests = [
        ("GET", "/api/runs", None),
        ("GET", f"/api/runs/{run_id}", None),
    ]
    for method, path, body in requests:
        r = db_client.request(method, path, json=body)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        assert r.json()["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# GET /api/runs (list)
# ---------------------------------------------------------------------------


def test_list_empty_table_returns_empty_envelope(db_client: TestClient, auth_headers: dict):
    r = db_client.get("/api/runs", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_list_returns_items_and_total_envelope(db_client: TestClient, auth_headers: dict):
    for _ in range(3):
        insert_run()

    r = db_client.get("/api/runs", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_item_shape_matches_agent_runs_columns(db_client: TestClient, auth_headers: dict):
    insert_run(
        task_kind="feature",
        task_summary="add a widget",
        engine="claude",
        reviewer_engine="codex",
        status="pr_opened",
        gate_ok=True,
        retries=1,
        review_verdict="APPROVE",
        cost_usd=1.5,
        pr_url="https://github.com/reinaldoq/nextlane/pull/1",
        worktree_branch="rails/add-a-widget-abc123",
    )

    r = db_client.get("/api/runs", headers=auth_headers)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["task_kind"] == "feature"
    assert item["task_summary"] == "add a widget"
    assert item["engine"] == "claude"
    assert item["reviewer_engine"] == "codex"
    assert item["status"] == "pr_opened"
    assert item["gate_ok"] is True
    assert item["retries"] == 1
    assert item["review_verdict"] == "APPROVE"
    assert item["cost_usd"] == 1.5
    assert item["pr_url"] == "https://github.com/reinaldoq/nextlane/pull/1"
    assert item["worktree_branch"] == "rails/add-a-widget-abc123"
    assert "id" in item
    assert "ts_iso" in item


def test_list_orders_newest_first(db_client: TestClient, auth_headers: dict):
    older = insert_run(task_summary="older run", ts_iso="2026-01-01T00:00:00+00:00")
    newer = insert_run(task_summary="newer run", ts_iso="2026-06-01T00:00:00+00:00")

    r = db_client.get("/api/runs", headers=auth_headers)
    ids = [item["id"] for item in r.json()["items"]]
    assert ids == [newer, older]


def test_list_respects_limit(db_client: TestClient, auth_headers: dict):
    for _ in range(3):
        insert_run()

    r = db_client.get("/api/runs", params={"limit": 2}, headers=auth_headers)
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3  # total reflects the whole table, not just the page


def test_list_default_limit_is_reasonable(db_client: TestClient, auth_headers: dict):
    for _ in range(5):
        insert_run()

    r = db_client.get("/api/runs", headers=auth_headers)
    assert len(r.json()["items"]) == 5  # well under the default limit


def test_list_limit_out_of_bounds_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.get("/api/runs", params={"limit": 0}, headers=auth_headers)
    assert r.status_code == 422

    r = db_client.get("/api/runs", params={"limit": 1000}, headers=auth_headers)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/runs/{id}
# ---------------------------------------------------------------------------


def test_get_run_returns_run_fields_and_empty_steps(db_client: TestClient, auth_headers: dict):
    run_id = insert_run(task_summary="add a widget")

    r = db_client.get(f"/api/runs/{run_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id
    assert body["task_summary"] == "add a widget"
    assert body["steps"] == []


def test_get_run_returns_steps_ordered_by_seq(db_client: TestClient, auth_headers: dict):
    run_id = insert_run()
    # inserted out of seq order -- the API must sort them, not echo insert order
    insert_step(run_id, 2, "builder", "ok", "did the work")
    insert_step(run_id, 1, "worktree", "ok", "branch ready")
    insert_step(run_id, 3, "gate", "failed", "pytest failed")

    r = db_client.get(f"/api/runs/{run_id}", headers=auth_headers)
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert [s["seq"] for s in steps] == [1, 2, 3]
    assert [s["phase"] for s in steps] == ["worktree", "builder", "gate"]
    assert steps[2]["status"] == "failed"
    assert steps[2]["detail"] == "pytest failed"


def test_get_run_steps_scoped_to_that_run_only(db_client: TestClient, auth_headers: dict):
    run_a = insert_run()
    run_b = insert_run()
    insert_step(run_a, 1, "worktree", "ok")
    insert_step(run_b, 1, "worktree", "ok")
    insert_step(run_b, 2, "builder", "ok")

    r = db_client.get(f"/api/runs/{run_a}", headers=auth_headers)
    assert len(r.json()["steps"]) == 1

    r = db_client.get(f"/api/runs/{run_b}", headers=auth_headers)
    assert len(r.json()["steps"]) == 2


def test_get_unknown_uuid_returns_404(db_client: TestClient, auth_headers: dict):
    import uuid

    r = db_client.get(f"/api/runs/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"]


def test_get_malformed_uuid_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.get("/api/runs/not-a-uuid", headers=auth_headers)
    assert r.status_code == 422
