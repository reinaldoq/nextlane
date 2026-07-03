import uuid
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .auth import current_user
from .db import pool
from .errors import api_error
from .transitions import can_transition

router = APIRouter(dependencies=[Depends(current_user)])

Status = Literal["available", "reserved", "sold"]

# Whitelisted sort columns -- user input is only ever used as a lookup key
# into this dict, NEVER interpolated directly into the ORDER BY clause.
SORT_COLUMNS = {"created_at", "price_cents", "year", "mileage_km"}
SORT_DIRECTIONS = {"asc", "desc"}


class VehicleIn(BaseModel):
    vin: str = Field(min_length=5, max_length=20)
    make: str = Field(min_length=1)
    model: str = Field(min_length=1)
    year: int = Field(ge=1950, le=2100)
    price_cents: int = Field(ge=0)
    mileage_km: int = Field(default=0, ge=0)
    status: Status = "available"


class VehiclePatch(BaseModel):
    vin: str | None = Field(default=None, min_length=5, max_length=20)
    make: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    year: int | None = Field(default=None, ge=1950, le=2100)
    price_cents: int | None = Field(default=None, ge=0)
    mileage_km: int | None = Field(default=None, ge=0)
    status: Status | None = None


class StatusIn(BaseModel):
    status: Status


def _parse_sort(sort: str) -> str:
    field, _, direction = sort.partition(":")
    if field not in SORT_COLUMNS or direction not in SORT_DIRECTIONS:
        raise api_error(422, "validation_error", "invalid sort", details={"sort": sort})
    return f"{field} {direction}"


@router.get("/vehicles")
def list_vehicles(
    q: str | None = None,
    status: Status | None = None,
    sort: str = "created_at:desc",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    order_sql = _parse_sort(sort)

    clauses: list[str] = []
    params: list = []
    if q:
        like = f"%{q}%"
        clauses.append("(make ILIKE %s OR model ILIKE %s OR vin ILIKE %s)")
        params.extend([like, like, like])
    if status:
        clauses.append("status = %s")
        params.append(status)
    where_sql = " AND ".join(clauses) if clauses else "true"

    sql = (
        f"SELECT *, count(*) over() as total FROM vehicles "
        f"WHERE {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s"
    )
    params.extend([limit, offset])

    with pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    total = rows[0]["total"] if rows else 0
    items = [{k: v for k, v in row.items() if k != "total"} for row in rows]
    return {"items": items, "total": total}


@router.post("/vehicles", status_code=201)
def create_vehicle(body: VehicleIn):
    sql = (
        "INSERT INTO vehicles (vin, make, model, year, price_cents, mileage_km, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *"
    )
    params = [
        body.vin,
        body.make,
        body.model,
        body.year,
        body.price_cents,
        body.mileage_km,
        body.status,
    ]
    try:
        with pool().connection() as conn:
            row = conn.execute(sql, params).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise api_error(
            409,
            "duplicate_vin",
            "a vehicle with this vin already exists",
            details={"vin": body.vin},
        ) from e
    return row


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute("SELECT * FROM vehicles WHERE id = %s", [vehicle_id]).fetchone()
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")
    return row


@router.patch("/vehicles/{vehicle_id}")
def patch_vehicle(vehicle_id: uuid.UUID, body: VehiclePatch):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_vehicle(vehicle_id)

    set_sql = ", ".join(f"{col} = %s" for col in fields)
    params = [*fields.values(), vehicle_id]
    sql = f"UPDATE vehicles SET {set_sql} WHERE id = %s RETURNING *"

    with pool().connection() as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")
    return row


@router.delete("/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute(
            "DELETE FROM vehicles WHERE id = %s RETURNING id", [vehicle_id]
        ).fetchone()
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")


@router.post("/vehicles/{vehicle_id}/status")
def set_vehicle_status(vehicle_id: uuid.UUID, body: StatusIn):
    new_status = body.status
    with pool().connection() as conn, conn.transaction():
        row = conn.execute(
            "SELECT status FROM vehicles WHERE id = %s FOR UPDATE", [vehicle_id]
        ).fetchone()
        if row is None:
            raise api_error(404, "not_found", "vehicle not found")

        current_status = row["status"]
        if not can_transition(current_status, new_status):
            raise api_error(
                422,
                "illegal_transition",
                f"cannot transition vehicle from {current_status} to {new_status}",
                details={"from": current_status, "to": new_status},
            )

        row = conn.execute(
            "UPDATE vehicles SET status = %s WHERE id = %s RETURNING *",
            [new_status, vehicle_id],
        ).fetchone()
    return row
