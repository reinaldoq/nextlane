import pytest
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api._lib import auth
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


@pytest.fixture()
def dead_jwks(monkeypatch, jwks_server):
    """Point auth at a dead JWKS endpoint with a fresh client cache.

    Depends on jwks_server so monkeypatch restores the live URL afterwards.
    """
    auth._jwk_clients.clear()
    monkeypatch.setenv("SUPABASE_JWKS_URL", "http://127.0.0.1:9/jwks.json")
    yield
    auth._jwk_clients.clear()


def _get(client: TestClient, token: str) -> object:
    return client.get("/probe/whoami", headers={"Authorization": f"Bearer {token}"})


def test_valid_token_returns_claims(client: TestClient, make_token):
    token = make_token(sub="11111111-1111-1111-1111-111111111111", email="reviewer@test.dev")
    r = _get(client, token)
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "11111111-1111-1111-1111-111111111111"
    assert body["email"] == "reviewer@test.dev"
    assert "www-authenticate" not in r.headers


def test_missing_authorization_header_is_401_unauthenticated(client: TestClient):
    r = client.get("/probe/whoami")
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_garbage_token_is_401(client: TestClient):
    r = client.get("/probe/whoami", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_wrong_audience_is_401(client: TestClient, make_token):
    r = _get(client, make_token(aud="anon"))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "InvalidAudienceError" in body["message"]


def test_expired_token_is_401(client: TestClient, make_token):
    r = _get(client, make_token(exp=1))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "ExpiredSignatureError" in body["message"]


def test_wrong_issuer_is_401(client: TestClient, make_token):
    r = _get(client, make_token(iss="https://evil.issuer/auth/v1"))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "InvalidIssuerError" in body["message"]


def test_token_without_exp_is_401(client: TestClient, make_token):
    r = _get(client, make_token(exp=None))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "MissingRequiredClaimError" in body["message"]


def test_hs256_alg_confusion_is_401(client: TestClient, make_token):
    # Same kid so key lookup succeeds; alg header says HS256 -> must be rejected.
    r = _get(client, make_token(key="x" * 32, alg="HS256"))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "InvalidAlgorithmError" in body["message"]


def test_wrong_signing_key_is_401(client: TestClient, make_token):
    # Correctly-formed ES256 token, right kid, but signed by a key NOT in the JWKS.
    r = _get(client, make_token(key=generate_private_key(SECP256R1())))
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "unauthenticated"
    assert "InvalidSignatureError" in body["message"]


def test_unknown_kid_is_401(client: TestClient, make_token):
    r = _get(client, make_token(kid="unknown-kid"))
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_jwks_outage_is_503_service_unavailable(client: TestClient, make_token, dead_jwks):
    r = _get(client, make_token())
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "service_unavailable"
    assert r.headers["Retry-After"] == "5"
