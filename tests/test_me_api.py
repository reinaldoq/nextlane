"""Tests for api/_lib/me.py: GET /api/me -- the caller's email + operator role.

The web calls this after login to decide whether to show the internal Mission
Control nav/route (operators only). Any authenticated user may call it; it only
ever reports on the caller. `reviewer@test.dev` is on OPERATOR_EMAILS (conftest),
a forged `dealer@test.dev` is not.
"""

from fastapi.testclient import TestClient


def test_me_401_without_token(db_client: TestClient):
    r = db_client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_me_reports_operator_true_for_allowlisted_email(db_client: TestClient, auth_headers: dict):
    r = db_client.get("/api/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "reviewer@test.dev"
    assert body["is_operator"] is True


def test_me_reports_operator_false_for_dealer(db_client: TestClient, make_token):
    dealer = {"Authorization": f"Bearer {make_token(email='dealer@test.dev')}"}
    r = db_client.get("/api/me", headers=dealer)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "dealer@test.dev"
    assert body["is_operator"] is False
