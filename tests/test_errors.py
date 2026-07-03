import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from api._lib.errors import api_error
from api.index import app, register_error_handlers


@pytest.fixture(scope="module")
def probe_app() -> FastAPI:
    """Fresh app wired with the production exception handlers plus probe routes.

    Keeps the shared `api.index.app` free of test-only routes.
    """
    application = register_error_handlers(FastAPI())

    @application.get("/probe/error")
    def probe_error():
        raise api_error(418, "teapot", "I am a teapot", {"foo": "bar"})

    @application.get("/probe/error-headers")
    def probe_error_headers():
        raise api_error(
            429,
            "rate_limited",
            "slow down",
            {"limit": 10},
            headers={"Retry-After": "30"},
        )

    @application.get("/probe/validate")
    def probe_validate(n: int):
        return {"n": n}

    class Widget(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def no_spaces(cls, v: str) -> str:
            if " " in v:
                raise ValueError("name must not contain spaces")
            return v

    @application.post("/probe/widgets")
    def probe_widgets(widget: Widget):
        return {"name": widget.name}

    @application.get("/probe/boom")
    def probe_boom():
        raise RuntimeError("boom")

    return application


@pytest.fixture(scope="module")
def client(probe_app: FastAPI) -> TestClient:
    return TestClient(probe_app)


def test_api_error_envelope_is_top_level(client: TestClient):
    r = client.get("/probe/error")
    assert r.status_code == 418
    assert r.json() == {
        "code": "teapot",
        "message": "I am a teapot",
        "details": {"foo": "bar"},
    }


def test_api_error_preserves_headers(client: TestClient):
    r = client.get("/probe/error-headers")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "30"
    assert r.json() == {
        "code": "rate_limited",
        "message": "slow down",
        "details": {"limit": 10},
    }


def test_validation_error_envelope(client: TestClient):
    r = client.get("/probe/validate", params={"n": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "invalid request"
    assert "errors" in body["details"]


def test_validation_error_from_field_validator_is_json_safe(client: TestClient):
    r = client.post("/probe/widgets", json={"name": "has spaces"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "invalid request"
    assert "errors" in body["details"]


def test_unknown_path_returns_enveloped_404():
    r = TestClient(app).get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"code": "error", "message": "Not Found", "details": {}}


def test_unhandled_exception_returns_enveloped_500(probe_app: FastAPI):
    r = TestClient(probe_app, raise_server_exceptions=False).get("/probe/boom")
    assert r.status_code == 500
    assert r.json() == {
        "code": "internal_error",
        "message": "internal server error",
        "details": {},
    }
