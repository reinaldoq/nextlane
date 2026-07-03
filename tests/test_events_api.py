import json
import os

import psycopg
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(clean):
    yield


def event_body(**overrides) -> dict:
    body = {
        "kind": "bug_report",
        "message": "the inventory page crashed on save",
        "context": {"page": "/inventory"},
    }
    body.update(overrides)
    return body


def _fetch_event(event_id: str) -> dict | None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("SELECT * FROM app_events WHERE id = %s", [event_id])
            return cur.fetchone()


# ---------------------------------------------------------------------------
# 401 without token
# ---------------------------------------------------------------------------


def test_401_without_token(db_client: TestClient):
    r = db_client.post("/api/events", json=event_body())
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# POST /api/events
# ---------------------------------------------------------------------------


def test_create_bug_report_returns_201_and_persists(db_client: TestClient, auth_headers: dict):
    body = event_body(kind="bug_report", message="crash on save")
    r = db_client.post("/api/events", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["id"]
    assert row["kind"] == "bug_report"
    assert row["message"] == "crash on save"
    assert row["context"] == {"page": "/inventory"}
    assert row["status"] == "new"
    assert row["created_at"]

    persisted = _fetch_event(row["id"])
    assert persisted is not None
    assert persisted["kind"] == "bug_report"
    assert persisted["message"] == "crash on save"
    assert persisted["status"] == "new"


def test_create_client_error_returns_201(db_client: TestClient, auth_headers: dict):
    r = db_client.post("/api/events", json=event_body(kind="client_error"), headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "client_error"


def test_create_client_error_with_multi_kb_nested_context_returns_201(
    db_client: TestClient, auth_headers: dict
):
    # Realistic client_error payload: a deep fake stack trace of ~8KB, well
    # within the 16KB context cap.
    frames = [
        {
            "file": f"/app/src/inventory/module_{i}.ts",
            "line": 100 + i,
            "col": 17,
            "func": f"handleVehicleSave_{i}",
            "source": "x" * 60,
        }
        for i in range(60)
    ]
    context = {
        "page": "/inventory",
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "error": {
            "name": "TypeError",
            "message": "cannot read properties of undefined",
            "stack": frames,
        },
    }
    size = len(json.dumps(context))
    assert 6_000 < size <= 16_384, f"fixture must be multi-KB but under the cap, got {size}"

    r = db_client.post(
        "/api/events", json=event_body(kind="client_error", context=context), headers=auth_headers
    )
    assert r.status_code == 201, r.text
    assert r.json()["context"] == context


def test_create_context_over_16kb_returns_422(db_client: TestClient, auth_headers: dict):
    context = {"page": "/inventory", "blob": "x" * 17_000}
    assert len(json.dumps(context)) > 16_384

    r = db_client.post("/api/events", json=event_body(context=context), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_unknown_extra_field_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.post("/api/events", json=event_body(severity="high"), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_omitted_context_defaults_to_empty_dict(db_client: TestClient, auth_headers: dict):
    body = event_body()
    del body["context"]
    r = db_client.post("/api/events", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["context"] == {}

    persisted = _fetch_event(r.json()["id"])
    assert persisted is not None
    assert persisted["context"] == {}


# ---------------------------------------------------------------------------
# 422 validation
# ---------------------------------------------------------------------------


def test_create_bad_kind_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.post("/api/events", json=event_body(kind="spam"), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_empty_message_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.post("/api/events", json=event_body(message=""), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_message_too_long_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.post("/api/events", json=event_body(message="x" * 4001), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_missing_message_returns_422(db_client: TestClient, auth_headers: dict):
    body = event_body()
    del body["message"]
    r = db_client.post("/api/events", json=body, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_31st_request_returns_429_and_other_user_unaffected(
    db_client: TestClient, make_token
):
    auth_a = {"Authorization": f"Bearer {make_token(sub='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')}"}
    for _ in range(30):
        r = db_client.post("/api/events", json=event_body(), headers=auth_a)
        assert r.status_code == 201, r.text

    r = db_client.post("/api/events", json=event_body(), headers=auth_a)
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    # Dynamic hint: seconds until the oldest request ages out of the window.
    retry_after = int(r.headers["retry-after"])
    assert 1 <= retry_after <= 60

    auth_b = {"Authorization": f"Bearer {make_token(sub='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')}"}
    r = db_client.post("/api/events", json=event_body(), headers=auth_b)
    assert r.status_code == 201, r.text
