from fastapi.testclient import TestClient

from api._lib.errors import api_error
from api.index import app

# Tiny throwaway probe routes added directly to the shared `app` instance so we
# can exercise the exception handlers' response envelope without touching
# api/index.py itself.


@app.get("/api/_probe/error")
def _probe_error():
    raise api_error(418, "teapot", "I am a teapot", {"foo": "bar"})


@app.get("/api/_probe/validate")
def _probe_validate(n: int):
    return {"n": n}


client = TestClient(app)


def test_api_error_envelope_is_top_level():
    r = client.get("/api/_probe/error")
    assert r.status_code == 418
    assert r.json() == {
        "code": "teapot",
        "message": "I am a teapot",
        "details": {"foo": "bar"},
    }


def test_validation_error_envelope():
    r = client.get("/api/_probe/validate", params={"n": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "invalid request"
    assert "errors" in body["details"]
