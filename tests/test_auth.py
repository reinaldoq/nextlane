import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api._lib.auth import current_user
from api.index import register_error_handlers


@pytest.fixture(scope="module")
def probe_app() -> FastAPI:
    """Fresh app wired with the production exception handlers plus a single
    route guarded by the `current_user` auth dependency.

    Keeps the shared `api.index.app` free of test-only routes.
    """
    application = register_error_handlers(FastAPI())

    @application.get("/probe/whoami")
    def probe_whoami(user: dict = Depends(current_user)):
        return user

    return application


@pytest.fixture()
def client(probe_app: FastAPI, jwks_server) -> TestClient:
    return TestClient(probe_app)


def test_valid_token_returns_claims(client: TestClient, make_token):
    token = make_token(sub="11111111-1111-1111-1111-111111111111", email="reviewer@test.dev")
    r = client.get("/probe/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "11111111-1111-1111-1111-111111111111"
    assert body["email"] == "reviewer@test.dev"


def test_missing_authorization_header_is_401_unauthenticated(client: TestClient):
    r = client.get("/probe/whoami")
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"


def test_garbage_token_is_401(client: TestClient):
    r = client.get("/probe/whoami", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_wrong_audience_is_401(client: TestClient, make_token):
    token = make_token(aud="anon")
    r = client.get("/probe/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_expired_token_is_401(client: TestClient, make_token):
    token = make_token(exp=1)
    r = client.get("/probe/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_wrong_issuer_is_401(client: TestClient, make_token):
    token = make_token(iss="https://evil.issuer/auth/v1")
    r = client.get("/probe/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"
