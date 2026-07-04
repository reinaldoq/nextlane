import csv
import io
import uuid
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import current_user
from .db import pool
from .errors import api_error
from .ratelimit import rate_limited
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
    # extra="forbid" so a PATCH carrying "status" (or any unknown field) is a 422
    # instead of being silently dropped; status changes must go through
    # POST /vehicles/{id}/status where the transition matrix is enforced.
    model_config = ConfigDict(extra="forbid")

    vin: str | None = Field(default=None, min_length=5, max_length=20)
    make: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    year: int | None = Field(default=None, ge=1950, le=2100)
    price_cents: int | None = Field(default=None, ge=0)
    mileage_km: int | None = Field(default=None, ge=0)


class StatusIn(BaseModel):
    status: Status


def _parse_sort(sort: str) -> str:
    field, _, direction = sort.partition(":")
    if field not in SORT_COLUMNS or direction not in SORT_DIRECTIONS:
        # Enumerate the whitelist in the error so an external caller that sent an
        # unsupported field/direction can self-correct without guessing.
        allowed_fields = sorted(SORT_COLUMNS)
        allowed_directions = sorted(SORT_DIRECTIONS)
        raise api_error(
            422,
            "validation_error",
            f"invalid sort '{sort}'; expected '<field>:<direction>' where field is one "
            f"of {allowed_fields} and direction is one of {allowed_directions}",
            details={
                "sort": sort,
                "allowed_fields": allowed_fields,
                "allowed_directions": allowed_directions,
            },
        )
    # id tiebreaker keeps pagination stable when rows tie on the sort key.
    return f"{field} {direction}, id desc"


def _like_pattern(q: str) -> str:
    """Escape LIKE metacharacters so q is matched literally (backslash is the default escape)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _raise_duplicate_vin(e: psycopg.errors.UniqueViolation, vin: str | None):
    """Map a vin unique violation to a 409; anything else is not ours -- re-raise (-> 500)."""
    if e.diag.constraint_name != "vehicles_vin_key":
        raise e
    raise api_error(
        409, "duplicate_vin", "a vehicle with this vin already exists", details={"vin": vin}
    ) from e


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
    where_params: list = []
    if q:
        like = _like_pattern(q)
        clauses.append("(make ILIKE %s OR model ILIKE %s OR vin ILIKE %s)")
        where_params.extend([like, like, like])
    if status:
        clauses.append("status = %s")
        where_params.append(status)
    where_sql = " AND ".join(clauses) if clauses else "true"

    sql = (
        f"SELECT *, count(*) over() as total FROM vehicles "
        f"WHERE {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s"
    )

    with pool().connection() as conn:
        rows = conn.execute(sql, [*where_params, limit, offset]).fetchall()
        if rows:
            total = rows[0]["total"]
        else:
            # Page is past the end (or no matches): the window function saw no
            # rows, so count separately for an honest total.
            total = conn.execute(
                f"SELECT count(*) AS total FROM vehicles WHERE {where_sql}", where_params
            ).fetchone()["total"]

    items = [{k: v for k, v in row.items() if k != "total"} for row in rows]
    return {"items": items, "total": total}


@router.post(
    "/vehicles", status_code=201, dependencies=[Depends(rate_limited(100, scope="writes"))]
)
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
        _raise_duplicate_vin(e, body.vin)
    return row


# Declared before GET /vehicles/{vehicle_id} so the literal "stats" segment
# wins the match instead of being parsed as a (malformed) uuid path param.
@router.get("/vehicles/stats")
def vehicle_stats():
    # One pass over the table: FILTER aggregates yield every status count plus
    # the grand total in a single query, never one COUNT per status.
    sql = (
        "SELECT "
        "count(*) FILTER (WHERE status = 'available') AS available, "
        "count(*) FILTER (WHERE status = 'reserved') AS reserved, "
        "count(*) FILTER (WHERE status = 'sold') AS sold, "
        "count(*) AS total "
        "FROM vehicles"
    )
    with pool().connection() as conn:
        return conn.execute(sql).fetchone()


# CSV column order is a stable external contract; price_eur is a display-only
# derivation of the integer-cents source of truth (cents/100, 2 decimals).
EXPORT_COLUMNS = ["vin", "make", "model", "year", "price_eur", "mileage_km", "status"]


# Declared before GET /vehicles/{vehicle_id} so the literal "export.csv" segment
# wins the match instead of being parsed as a (malformed) uuid path param.
@router.get("/vehicles/export.csv")
def export_vehicles_csv():
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT vin, make, model, year, price_cents, mileage_km, status "
            "FROM vehicles ORDER BY created_at DESC, id DESC"
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row["vin"],
                row["make"],
                row["model"],
                row["year"],
                f"{row['price_cents'] / 100:.2f}",
                row["mileage_km"],
                row["status"],
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vehicles.csv"'},
    )


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute("SELECT * FROM vehicles WHERE id = %s", [vehicle_id]).fetchone()
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")
    return row


@router.patch("/vehicles/{vehicle_id}", dependencies=[Depends(rate_limited(100, scope="writes"))])
def patch_vehicle(vehicle_id: uuid.UUID, body: VehiclePatch):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_vehicle(vehicle_id)

    set_sql = ", ".join(f"{col} = %s" for col in fields)
    params = [*fields.values(), vehicle_id]
    sql = f"UPDATE vehicles SET {set_sql} WHERE id = %s RETURNING *"

    try:
        with pool().connection() as conn:
            row = conn.execute(sql, params).fetchone()
    except psycopg.errors.UniqueViolation as e:
        _raise_duplicate_vin(e, fields.get("vin"))
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")
    return row


@router.delete(
    "/vehicles/{vehicle_id}",
    status_code=204,
    dependencies=[Depends(rate_limited(100, scope="writes"))],
)
def delete_vehicle(vehicle_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute(
            "DELETE FROM vehicles WHERE id = %s RETURNING id", [vehicle_id]
        ).fetchone()
    if row is None:
        raise api_error(404, "not_found", "vehicle not found")


@router.post(
    "/vehicles/{vehicle_id}/status", dependencies=[Depends(rate_limited(100, scope="writes"))]
)
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
