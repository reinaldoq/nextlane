"""Tests for rails.events: Supabase app_events access via the REST API.

Spec ref: Phase-2 Task 8. `fetch_new_events` / `mark_event` are the ONLY
network-touching seam here -- `opener` is injected (default
`urllib.request.urlopen`) so every test below runs with a fake opener that
records the `urllib.request.Request` it was given and returns canned bytes.
No real network, no real Supabase project, no real credentials.

SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are read from the environment --
populated locally from `.env` (service-role key bypasses RLS) and NEVER set
in CI (see rails/events.py's module docstring and .env.example).
"""

from __future__ import annotations

import contextlib
import io
import json

import pytest

from rails.events import AppEvent, EventsError, fetch_new_events, mark_event


def make_opener(body: bytes, calls: list | None = None):
    """A fake opener: records the Request it was given (if `calls` is
    passed) and returns a context-managed BytesIO of `body`, mirroring
    urlopen's context-manager response object."""

    def _opener(request):
        if calls is not None:
            calls.append(request)
        return contextlib.closing(io.BytesIO(body))

    return _opener


ROWS = [
    {
        "id": "11111111-1111-1111-1111-111111111111",
        "kind": "bug_report",
        "message": "Save button does nothing on the vehicle form",
        "context": {"page": "/inventory", "browser": "Safari"},
        "status": "new",
        "created_at": "2026-07-03T12:00:00+00:00",
    },
    {
        "id": "22222222-2222-2222-2222-222222222222",
        "kind": "client_error",
        "message": "TypeError: cannot read properties of undefined",
        "context": {"stack": "at Foo (bar.js:1:1)"},
        "status": "new",
        "created_at": "2026-07-03T11:00:00+00:00",
    },
]


@pytest.fixture(autouse=True)
def supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")


# --- fetch_new_events --------------------------------------------------------


def test_fetch_new_events_parses_canned_rows_into_app_events():
    opener = make_opener(json.dumps(ROWS).encode("utf-8"))

    events = fetch_new_events(opener=opener)

    assert len(events) == 2
    first = events[0]
    assert isinstance(first, AppEvent)
    assert first.id == "11111111-1111-1111-1111-111111111111"
    assert first.kind == "bug_report"
    assert first.message == "Save button does nothing on the vehicle form"
    assert first.context == {"page": "/inventory", "browser": "Safari"}
    assert first.status == "new"
    assert first.created_at == "2026-07-03T12:00:00+00:00"


def test_fetch_new_events_defaults_missing_context_to_empty_dict():
    row = dict(ROWS[0])
    row["context"] = None
    opener = make_opener(json.dumps([row]).encode("utf-8"))

    events = fetch_new_events(opener=opener)

    assert events[0].context == {}


def test_fetch_new_events_builds_correct_url_and_headers():
    calls: list = []
    opener = make_opener(json.dumps(ROWS).encode("utf-8"), calls=calls)

    fetch_new_events(limit=10, opener=opener)

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == (
        "https://example.supabase.co/rest/v1/app_events"
        "?status=eq.new&order=created_at.desc&limit=10"
    )
    assert request.get_header("Apikey") == "test-service-role-key"
    assert request.get_header("Authorization") == "Bearer test-service-role-key"


def test_fetch_new_events_honors_custom_limit():
    calls: list = []
    opener = make_opener(json.dumps(ROWS).encode("utf-8"), calls=calls)

    fetch_new_events(limit=3, opener=opener)

    assert "limit=3" in calls[0].full_url


def test_fetch_new_events_empty_result():
    opener = make_opener(b"[]")

    assert fetch_new_events(opener=opener) == []


# --- mark_event --------------------------------------------------------------


def test_mark_event_builds_correct_url_method_and_body():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    mark_event("11111111-1111-1111-1111-111111111111", "triaged", opener=opener)

    assert len(calls) == 1
    request = calls[0]
    assert request.get_method() == "PATCH"
    assert request.full_url == (
        "https://example.supabase.co/rest/v1/app_events?id=eq.11111111-1111-1111-1111-111111111111"
    )
    assert json.loads(request.data) == {"status": "triaged"}
    assert request.get_header("Prefer") == "return=minimal"
    assert request.get_header("Apikey") == "test-service-role-key"
    assert request.get_header("Authorization") == "Bearer test-service-role-key"


def test_mark_event_content_type_is_json():
    calls: list = []
    opener = make_opener(b"", calls=calls)

    mark_event("some-id", "resolved", opener=opener)

    assert calls[0].get_header("Content-type") == "application/json"


# --- missing env: clear errors ------------------------------------------------


def test_fetch_new_events_missing_supabase_url_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(EventsError, match="SUPABASE_URL"):
        fetch_new_events(opener=make_opener(b"[]"))


def test_fetch_new_events_missing_service_role_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(EventsError, match="SUPABASE_SERVICE_ROLE_KEY"):
        fetch_new_events(opener=make_opener(b"[]"))


def test_mark_event_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(EventsError, match="SUPABASE_URL"):
        mark_event("some-id", "triaged", opener=make_opener(b""))
