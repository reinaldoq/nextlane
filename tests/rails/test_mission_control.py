"""Tests for rails.mission_control: best-effort agent-run telemetry written
to the HOSTED Supabase project (`agent_runs` / `run_steps`) via PostgREST
service-role writes.

Spec ref: Mission Control (the one sanctioned post-Phase-1-freeze app
addition, design spec Sec6/Sec12) -- a live in-app dashboard reading these
tables through `api/_lib/runs.py`. This module is the WRITE side, driven by
`rails/agents/loop.py`.

Same injection contract as `rails.events`: `opener` (default
`urllib.request.urlopen`) is the sole network seam, injected on every call --
every test below runs with a fake opener that records the
`urllib.request.Request` it was given and returns canned bytes. No real
network, no real Supabase project, no real credentials.

CRITICAL invariant this module must uphold (enforced at the rails.agents.loop
call sites, not here): every one of these calls is used BEST-EFFORT --
`MissionControlError` (missing env) or any opener/network failure must be
catchable and non-fatal to the run. This file only proves the request-building
contract in isolation; `tests/rails/test_loop.py` proves the loop survives a
raising/faking mission-control layer.
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from rails.mission_control import MissionControlError, add_step, finish_run, start_run


def make_opener(body: bytes, calls: list | None = None):
    """A fake opener: records the Request it was given (if `calls` is
    passed) and returns a context-managed BytesIO of `body`, mirroring
    urlopen's context-manager response object."""

    def _opener(request):
        if calls is not None:
            calls.append(request)
        return contextlib.closing(io.BytesIO(body))

    return _opener


@pytest.fixture(autouse=True)
def supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")


RUN_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "ts_iso": "2026-07-04T12:00:00+00:00",
    "task_kind": "feature",
    "task_summary": "add a widget",
    "engine": "claude",
    "reviewer_engine": "codex",
    "status": "running",
}


# --- start_run ---------------------------------------------------------


def test_start_run_returns_new_run_id():
    opener = make_opener(json.dumps([RUN_ROW]).encode("utf-8"))

    run_id = start_run(
        {
            "task_kind": "feature",
            "task_summary": "add a widget",
            "engine": "claude",
            "reviewer_engine": "codex",
            "worktree_branch": "rails/add-a-widget-abc123",
            "status": "running",
        },
        opener=opener,
    )

    assert run_id == "11111111-1111-1111-1111-111111111111"


def test_start_run_builds_correct_url_method_body_and_headers():
    calls: list = []
    opener = make_opener(json.dumps([RUN_ROW]).encode("utf-8"), calls=calls)

    start_run(
        {
            "task_kind": "feature",
            "task_summary": "add a widget",
            "engine": "claude",
            "reviewer_engine": "codex",
            "worktree_branch": "rails/add-a-widget-abc123",
            "status": "running",
        },
        opener=opener,
    )

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://example.supabase.co/rest/v1/agent_runs"
    assert json.loads(request.data) == {
        "task_kind": "feature",
        "task_summary": "add a widget",
        "engine": "claude",
        "reviewer_engine": "codex",
        "worktree_branch": "rails/add-a-widget-abc123",
        "status": "running",
    }
    assert request.get_header("Apikey") == "test-service-role-key"
    assert request.get_header("Authorization") == "Bearer test-service-role-key"
    assert request.get_header("Content-type") == "application/json"
    # return=representation -- we need the inserted row's id back.
    assert request.get_header("Prefer") == "return=representation"


def test_start_run_missing_supabase_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(MissionControlError, match="SUPABASE_URL"):
        start_run({"task_kind": "feature"}, opener=make_opener(b"[]"))


def test_start_run_missing_service_role_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(MissionControlError, match="SUPABASE_SERVICE_ROLE_KEY"):
        start_run({"task_kind": "feature"}, opener=make_opener(b"[]"))


# --- add_step ------------------------------------------------------------


def test_add_step_builds_correct_url_method_body_and_headers():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    add_step(
        "11111111-1111-1111-1111-111111111111",
        1,
        "worktree",
        "ok",
        "branch rails/add-a-widget-abc123 ready",
        opener=opener,
    )

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://example.supabase.co/rest/v1/run_steps"
    assert json.loads(request.data) == {
        "run_id": "11111111-1111-1111-1111-111111111111",
        "seq": 1,
        "phase": "worktree",
        "status": "ok",
        "detail": "branch rails/add-a-widget-abc123 ready",
    }
    assert request.get_header("Prefer") == "return=minimal"
    assert request.get_header("Apikey") == "test-service-role-key"
    assert request.get_header("Authorization") == "Bearer test-service-role-key"


def test_add_step_detail_defaults_to_none():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    add_step("some-run-id", 2, "gate", "started", opener=opener)

    assert json.loads(calls[0].data)["detail"] is None


def test_add_step_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(MissionControlError, match="SUPABASE_URL"):
        add_step("some-run-id", 1, "gate", "ok", opener=make_opener(b""))


# --- finish_run ------------------------------------------------------------


def test_finish_run_builds_correct_url_method_and_body():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    finish_run(
        "11111111-1111-1111-1111-111111111111",
        status="pr_opened",
        gate_ok=True,
        review_verdict="APPROVE",
        cost_usd=0.42,
        pr_url="https://github.com/reinaldoq/nextlane/pull/1",
        retries=1,
        opener=opener,
        now_fn=lambda: "2026-07-04T12:34:56+00:00",
    )

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "PATCH"
    assert request.full_url == (
        "https://example.supabase.co/rest/v1/agent_runs?id=eq.11111111-1111-1111-1111-111111111111"
    )
    assert json.loads(request.data) == {
        "status": "pr_opened",
        "gate_ok": True,
        "review_verdict": "APPROVE",
        "cost_usd": 0.42,
        "pr_url": "https://github.com/reinaldoq/nextlane/pull/1",
        "retries": 1,
        "finished_at": "2026-07-04T12:34:56+00:00",
    }
    assert request.get_header("Prefer") == "return=minimal"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Apikey") == "test-service-role-key"
    assert request.get_header("Authorization") == "Bearer test-service-role-key"


def test_finish_run_omits_retries_when_not_given():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    finish_run(
        "some-run-id",
        status="gate_failed",
        gate_ok=False,
        review_verdict=None,
        cost_usd=None,
        pr_url=None,
        opener=opener,
        now_fn=lambda: "2026-07-04T00:00:00+00:00",
    )

    body = json.loads(calls[0].data)
    assert "retries" not in body
    assert body["status"] == "gate_failed"
    assert body["gate_ok"] is False
    assert body["pr_url"] is None


def test_finish_run_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(MissionControlError, match="SUPABASE_SERVICE_ROLE_KEY"):
        finish_run(
            "some-run-id",
            status="error",
            gate_ok=None,
            review_verdict=None,
            cost_usd=None,
            pr_url=None,
            opener=make_opener(b""),
        )
