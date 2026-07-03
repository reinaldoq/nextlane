import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.index import app


@pytest.fixture(autouse=True)
def _clean(clean_tables):
    yield


@pytest.fixture()
def client(jwks_server) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def auth(make_token) -> dict:
    return {"Authorization": f"Bearer {make_token()}"}


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


def test_401_without_token(client: TestClient):
    r = client.post("/api/events", json=event_body())
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# POST /api/events
# ---------------------------------------------------------------------------


def test_create_bug_report_returns_201_and_persists(client: TestClient, auth: dict):
    body = event_body(kind="bug_report", message="crash on save")
    r = client.post("/api/events", json=body, headers=auth)
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


def test_create_client_error_returns_201(client: TestClient, auth: dict):
    r = client.post("/api/events", json=event_body(kind="client_error"), headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "client_error"


# ---------------------------------------------------------------------------
# 422 validation
# ---------------------------------------------------------------------------


def test_create_bad_kind_returns_422(client: TestClient, auth: dict):
    r = client.post("/api/events", json=event_body(kind="spam"), headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_empty_message_returns_422(client: TestClient, auth: dict):
    r = client.post("/api/events", json=event_body(message=""), headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_message_too_long_returns_422(client: TestClient, auth: dict):
    r = client.post("/api/events", json=event_body(message="x" * 4001), headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_create_missing_message_returns_422(client: TestClient, auth: dict):
    body = event_body()
    del body["message"]
    r = client.post("/api/events", json=body, headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_31st_request_returns_429_and_other_user_unaffected(
    client: TestClient, make_token
):
    auth_a = {"Authorization": f"Bearer {make_token(sub='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')}"}
    for _ in range(30):
        r = client.post("/api/events", json=event_body(), headers=auth_a)
        assert r.status_code == 201, r.text

    r = client.post("/api/events", json=event_body(), headers=auth_a)
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert r.headers.get("retry-after") == "60"

    auth_b = {"Authorization": f"Bearer {make_token(sub='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')}"}
    r = client.post("/api/events", json=event_body(), headers=auth_b)
    assert r.status_code == 201, r.text
