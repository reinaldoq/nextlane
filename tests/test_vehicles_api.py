import os
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(clean):
    yield


def unique_vin() -> str:
    return uuid.uuid4().hex[:17].upper()


def vehicle_body(**overrides) -> dict:
    body = {
        "vin": unique_vin(),
        "make": "Honda",
        "model": "Accord",
        "year": 2020,
        "price_cents": 1_500_000,
        "mileage_km": 12_000,
    }
    body.update(overrides)
    return body


def create_vehicle(db_client: TestClient, auth_headers: dict, **overrides) -> dict:
    r = db_client.post("/api/vehicles", json=vehicle_body(**overrides), headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 401 without token, on every route
# ---------------------------------------------------------------------------


def test_401_without_token_on_every_route(db_client: TestClient):
    vid = "00000000-0000-0000-0000-000000000000"
    requests = [
        ("GET", "/api/vehicles", None),
        ("POST", "/api/vehicles", vehicle_body()),
        ("GET", f"/api/vehicles/{vid}", None),
        ("PATCH", f"/api/vehicles/{vid}", {"price_cents": 1}),
        ("DELETE", f"/api/vehicles/{vid}", None),
        ("POST", f"/api/vehicles/{vid}/status", {"status": "reserved"}),
    ]
    for method, path, body in requests:
        r = db_client.request(method, path, json=body)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        assert r.json()["code"] == "unauthenticated"


# ---------------------------------------------------------------------------
# POST /api/vehicles
# ---------------------------------------------------------------------------


def test_create_vehicle_returns_201_full_row(db_client: TestClient, auth_headers: dict):
    body = vehicle_body()
    r = db_client.post("/api/vehicles", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    row = r.json()
    assert uuid.UUID(row["id"])
    assert row["vin"] == body["vin"]
    assert row["make"] == "Honda"
    assert row["model"] == "Accord"
    assert row["year"] == 2020
    assert row["status"] == "available"
    assert row["price_cents"] == 1_500_000
    assert isinstance(row["price_cents"], int)
    assert row["mileage_km"] == 12_000


def test_create_duplicate_vin_returns_409(db_client: TestClient, auth_headers: dict):
    vin = unique_vin()
    create_vehicle(db_client, auth_headers, vin=vin)
    r = db_client.post("/api/vehicles", json=vehicle_body(vin=vin), headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "duplicate_vin"


@pytest.mark.parametrize(
    "overrides",
    [
        {"vin": "abc"},
        {"year": 1800},
        {"price_cents": -100},
        {"vin": "abc", "year": 1800, "price_cents": -100},
    ],
)
def test_create_invalid_body_returns_422_validation_error(
    db_client: TestClient, auth_headers: dict, overrides: dict
):
    r = db_client.post("/api/vehicles", json=vehicle_body(**overrides), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# GET /api/vehicles (list)
# ---------------------------------------------------------------------------


def test_list_returns_items_and_total_envelope(db_client: TestClient, auth_headers: dict):
    for _ in range(3):
        create_vehicle(db_client, auth_headers)

    r = db_client.get("/api/vehicles", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_pagination_limit_offset(db_client: TestClient, auth_headers: dict):
    for _ in range(3):
        create_vehicle(db_client, auth_headers)

    r = db_client.get("/api/vehicles", params={"limit": 2}, headers=auth_headers)
    body = r.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3

    r = db_client.get("/api/vehicles", params={"limit": 2, "offset": 2}, headers=auth_headers)
    body = r.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3


def test_list_offset_past_end_reports_true_total(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers)
    create_vehicle(db_client, auth_headers)

    r = db_client.get("/api/vehicles", params={"offset": 50}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 2}


def _insert_vehicles_with_identical_created_at(n: int) -> set[str]:
    """Bypass the API: identical created_at forces the sort-key tie the API can't create."""
    ids: set[str] = set()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for _ in range(n):
            row = conn.execute(
                "insert into vehicles (vin, make, model, year, price_cents, created_at) "
                "values (%s, 'Tie', 'Breaker', 2020, 1000, "
                "timestamptz '2026-01-01 00:00:00+00') returning id",
                [unique_vin()],
            ).fetchone()
            ids.add(str(row[0]))
    return ids


def test_list_pagination_is_stable_when_created_at_ties(db_client: TestClient, auth_headers: dict):
    expected = _insert_vehicles_with_identical_created_at(5)

    seen: list[str] = []
    for offset in (0, 2, 4):
        r = db_client.get(
            "/api/vehicles", params={"limit": 2, "offset": offset}, headers=auth_headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        seen.extend(v["id"] for v in body["items"])

    assert len(seen) == len(set(seen)), f"duplicate ids across pages: {seen}"
    assert set(seen) == expected, "pages must cover every row exactly once"


def test_list_q_matches_make_case_insensitive(db_client: TestClient, auth_headers: dict):
    target = create_vehicle(db_client, auth_headers, make="Toyota", model="Camry")
    create_vehicle(db_client, auth_headers, make="Honda", model="Civic")

    r = db_client.get("/api/vehicles", params={"q": "toyota"}, headers=auth_headers)
    ids = {v["id"] for v in r.json()["items"]}
    assert ids == {target["id"]}


def test_list_q_matches_model_case_insensitive(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers, make="Toyota", model="Camry")
    target = create_vehicle(db_client, auth_headers, make="Honda", model="Civic")

    r = db_client.get("/api/vehicles", params={"q": "CIVIC"}, headers=auth_headers)
    ids = {v["id"] for v in r.json()["items"]}
    assert ids == {target["id"]}


def test_list_q_matches_vin_case_insensitive(db_client: TestClient, auth_headers: dict):
    vin = "ZZZUNIQUEVIN99999"
    target = create_vehicle(db_client, auth_headers, vin=vin)
    create_vehicle(db_client, auth_headers)

    r = db_client.get("/api/vehicles", params={"q": vin.lower()}, headers=auth_headers)
    ids = {v["id"] for v in r.json()["items"]}
    assert ids == {target["id"]}


@pytest.mark.parametrize("literal_make, q", [("A%B", "A%B"), ("A_B", "A_B")])
def test_list_q_treats_like_metacharacters_literally(
    db_client: TestClient, auth_headers: dict, literal_make: str, q: str
):
    target = create_vehicle(db_client, auth_headers, make=literal_make)
    create_vehicle(db_client, auth_headers, make="AXB")  # would match if %/_ acted as wildcards

    r = db_client.get("/api/vehicles", params={"q": q}, headers=auth_headers)
    ids = {v["id"] for v in r.json()["items"]}
    assert ids == {target["id"]}


def test_list_filters_by_status(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers)
    reserved = create_vehicle(db_client, auth_headers)
    r = db_client.post(
        f"/api/vehicles/{reserved['id']}/status", json={"status": "reserved"}, headers=auth_headers
    )
    assert r.status_code == 200

    r = db_client.get("/api/vehicles", params={"status": "reserved"}, headers=auth_headers)
    items = r.json()["items"]
    assert {v["id"] for v in items} == {reserved["id"]}
    assert all(v["status"] == "reserved" for v in items)


def test_list_sort_price_cents_desc(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers, price_cents=1000)
    create_vehicle(db_client, auth_headers, price_cents=3000)
    create_vehicle(db_client, auth_headers, price_cents=2000)

    r = db_client.get("/api/vehicles", params={"sort": "price_cents:desc"}, headers=auth_headers)
    prices = [v["price_cents"] for v in r.json()["items"]]
    assert prices == [3000, 2000, 1000]


def test_list_sort_year_asc(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers, year=2022)
    create_vehicle(db_client, auth_headers, year=1999)
    create_vehicle(db_client, auth_headers, year=2010)

    r = db_client.get("/api/vehicles", params={"sort": "year:asc"}, headers=auth_headers)
    years = [v["year"] for v in r.json()["items"]]
    assert years == [1999, 2010, 2022]


def test_list_sort_unknown_field_returns_422(db_client: TestClient, auth_headers: dict):
    create_vehicle(db_client, auth_headers)
    r = db_client.get("/api/vehicles", params={"sort": "evil:asc"}, headers=auth_headers)
    assert r.status_code == 422


def test_list_default_sort_is_created_at_desc(db_client: TestClient, auth_headers: dict):
    v1 = create_vehicle(db_client, auth_headers)
    time.sleep(0.01)
    v2 = create_vehicle(db_client, auth_headers)
    time.sleep(0.01)
    v3 = create_vehicle(db_client, auth_headers)

    r = db_client.get("/api/vehicles", headers=auth_headers)
    ids = [v["id"] for v in r.json()["items"]]
    assert ids == [v3["id"], v2["id"], v1["id"]]


# ---------------------------------------------------------------------------
# GET /api/vehicles/{id}
# ---------------------------------------------------------------------------


def test_get_by_id_returns_200(db_client: TestClient, auth_headers: dict):
    v = create_vehicle(db_client, auth_headers)
    r = db_client.get(f"/api/vehicles/{v['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == v["id"]


def test_get_unknown_uuid_returns_404(db_client: TestClient, auth_headers: dict):
    r = db_client.get(f"/api/vehicles/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"]


def test_get_malformed_uuid_returns_422(db_client: TestClient, auth_headers: dict):
    # Consistent choice across the router: malformed path uuids are a 422
    # validation error (FastAPI's own path-param parsing), not a 404.
    r = db_client.get("/api/vehicles/not-a-uuid", headers=auth_headers)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/vehicles/{id}
# ---------------------------------------------------------------------------


def test_patch_updates_subset(db_client: TestClient, auth_headers: dict):
    v = create_vehicle(db_client, auth_headers, price_cents=1000)
    r = db_client.patch(
        f"/api/vehicles/{v['id']}", json={"price_cents": 5000}, headers=auth_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["price_cents"] == 5000
    assert body["make"] == v["make"]
    assert body["vin"] == v["vin"]


def test_patch_unknown_id_returns_404(db_client: TestClient, auth_headers: dict):
    r = db_client.patch(
        f"/api/vehicles/{uuid.uuid4()}", json={"price_cents": 1}, headers=auth_headers
    )
    assert r.status_code == 404


def test_patch_malformed_uuid_returns_422(db_client: TestClient, auth_headers: dict):
    r = db_client.patch("/api/vehicles/not-a-uuid", json={"price_cents": 1}, headers=auth_headers)
    assert r.status_code == 422


def test_patch_status_is_rejected_and_does_not_bypass_transitions(
    db_client: TestClient, auth_headers: dict
):
    v = create_vehicle(db_client, auth_headers)
    r = db_client.post(
        f"/api/vehicles/{v['id']}/status", json={"status": "sold"}, headers=auth_headers
    )
    assert r.status_code == 200

    # status is not a patchable field; the only path is POST /{id}/status.
    r = db_client.patch(
        f"/api/vehicles/{v['id']}", json={"status": "available"}, headers=auth_headers
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"

    r = db_client.get(f"/api/vehicles/{v['id']}", headers=auth_headers)
    assert r.json()["status"] == "sold"


def test_patch_vin_to_existing_vin_returns_409(db_client: TestClient, auth_headers: dict):
    v1 = create_vehicle(db_client, auth_headers)
    v2 = create_vehicle(db_client, auth_headers)

    r = db_client.patch(f"/api/vehicles/{v2['id']}", json={"vin": v1["vin"]}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "duplicate_vin"


# ---------------------------------------------------------------------------
# DELETE /api/vehicles/{id}
# ---------------------------------------------------------------------------


def test_delete_returns_204_then_404_on_get(db_client: TestClient, auth_headers: dict):
    v = create_vehicle(db_client, auth_headers)
    r = db_client.delete(f"/api/vehicles/{v['id']}", headers=auth_headers)
    assert r.status_code == 204
    assert r.content == b""

    r = db_client.get(f"/api/vehicles/{v['id']}", headers=auth_headers)
    assert r.status_code == 404


def test_delete_unknown_id_returns_404(db_client: TestClient, auth_headers: dict):
    r = db_client.delete(f"/api/vehicles/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/vehicles/{id}/status
# ---------------------------------------------------------------------------


def test_status_available_to_reserved_returns_200_new_status(
    db_client: TestClient, auth_headers: dict
):
    v = create_vehicle(db_client, auth_headers)
    r = db_client.post(
        f"/api/vehicles/{v['id']}/status", json={"status": "reserved"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "reserved"
    assert r.json()["id"] == v["id"]


def test_status_illegal_transition_returns_422(db_client: TestClient, auth_headers: dict):
    v = create_vehicle(db_client, auth_headers)
    r = db_client.post(
        f"/api/vehicles/{v['id']}/status", json={"status": "sold"}, headers=auth_headers
    )
    assert r.status_code == 200

    r = db_client.post(
        f"/api/vehicles/{v['id']}/status", json={"status": "available"}, headers=auth_headers
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "illegal_transition"
    assert body["details"] == {"from": "sold", "to": "available"}


def test_status_unknown_id_returns_404(db_client: TestClient, auth_headers: dict):
    r = db_client.post(
        f"/api/vehicles/{uuid.uuid4()}/status", json={"status": "reserved"}, headers=auth_headers
    )
    assert r.status_code == 404
