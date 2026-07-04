---
name: scaffold-module
description: Generate a new DMS module following the vehicles reference pattern — migration, router, page, tests
---

# scaffold-module

Generate a complete, working DMS module (migration + API router + web page +
tests) that follows every convention in `AGENTS.md`, by copying the shape of
the `vehicles` reference module at each step. Read `AGENTS.md` first if you
haven't already — this skill operationalizes its "module pattern" section.

Use this when asked to add a new resource to the app (e.g. "scaffold a parts
module with fields sku, name, price_cents, qty_on_hand").

Throughout this skill, `<module>` is the lowercase plural snake_case table/
route name (e.g. `parts`), `<Module>` is the PascalCase singular name (e.g.
`Part`), and the worked example used in every skeleton below is exactly that
parts module: `sku` (text, unique), `name` (text), `price_cents` (bigint,
money-in-cents), `qty_on_hand` (int, default 0). Substitute your own module
name and fields; keep the shape identical.

If your module has a lifecycle/status field (like `vehicles.status`), don't
put it in `<Module>Patch` — read the "Status/lifecycle fields" callout at the
end of Step 2 and repeat the `vehicles` transition-matrix pattern
(`api/_lib/transitions.py`, the `POST /{id}/status` route) instead.

## Step 0 — confirm the shape

Before writing anything, pin down:
- the table/route name (plural, snake_case) and the singular PascalCase name
  for Pydantic/TS types
- every field, its SQL type, and its constraints (required? default? check
  constraint?)
- whether any field is money (→ `<field>_cents` as `bigint`, never a float)
  or a lifecycle/status field (→ dedicated transition endpoint, not PATCH)
- what you'll let callers search by (free-text `q`) and sort by (the
  whitelist)

For the worked example: table `parts`, singular `Part`, fields `sku` (text,
unique, 1-40 chars), `name` (text, 1-200 chars), `price_cents` (bigint, >=0),
`qty_on_hand` (int, >=0, default 0). Search by `q` over `sku`/`name`; sort
whitelist `{created_at, price_cents, qty_on_hand}`.

## Step 1 — migration

Create the migration file with the Supabase CLI so the timestamp prefix is
correct:

```bash
supabase migration new <module>
# -> supabase/migrations/<timestamp>_<module>.sql
```

Write the SQL modeled on `supabase/migrations/20260703152303_init_inventory.sql`:
RLS enabled with **no policies** (deny-by-default), check constraints for
every invariant, indexes for anything filtered/sorted, and an `updated_at`
trigger. The `set_updated_at()` trigger function already exists (created
alongside the `vehicles` table) — reuse it, don't redefine it.

```sql
create table parts (
  id uuid primary key default gen_random_uuid(),
  sku text not null unique check (char_length(sku) between 1 and 40),
  name text not null check (char_length(name) between 1 and 200),
  price_cents bigint not null check (price_cents >= 0),
  qty_on_hand int not null default 0 check (qty_on_hand >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index parts_created_idx on parts (created_at desc);
create index parts_qty_idx on parts (qty_on_hand);

create trigger parts_updated_at before update on parts
  for each row execute function set_updated_at();

-- RLS deny-by-default: no policies => anon key gets nothing via PostgREST.
alter table parts enable row level security;
```

Apply it locally to prove it runs: `supabase db reset` (equivalently
`just seed`).

## Step 2 — router (`api/_lib/<module>.py`)

Copy `api/_lib/vehicles.py`'s shape exactly: router-level auth dependency,
`<Module>In`/`<Module>Patch` Pydantic models (`Patch` uses
`extra="forbid"`), a sort whitelist, parameterized SQL throughout,
`RETURNING *` on every write, and `rate_limited(..., scope="writes")` on
every write route.

```python
import uuid

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .auth import current_user
from .db import pool
from .errors import api_error
from .ratelimit import rate_limited

router = APIRouter(dependencies=[Depends(current_user)])

# Whitelisted sort columns -- user input is only ever used as a lookup key
# into this dict, NEVER interpolated directly into the ORDER BY clause.
SORT_COLUMNS = {"created_at", "price_cents", "qty_on_hand"}
SORT_DIRECTIONS = {"asc", "desc"}


class PartIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0)
    qty_on_hand: int = Field(default=0, ge=0)


class PartPatch(BaseModel):
    # extra="forbid" so a PATCH carrying an unknown field is a 422 instead of
    # being silently dropped.
    model_config = ConfigDict(extra="forbid")

    sku: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_cents: int | None = Field(default=None, ge=0)
    qty_on_hand: int | None = Field(default=None, ge=0)


def _parse_sort(sort: str) -> str:
    field, _, direction = sort.partition(":")
    if field not in SORT_COLUMNS or direction not in SORT_DIRECTIONS:
        raise api_error(422, "validation_error", "invalid sort", details={"sort": sort})
    # id tiebreaker keeps pagination stable when rows tie on the sort key.
    return f"{field} {direction}, id desc"


def _like_pattern(q: str) -> str:
    """Escape LIKE metacharacters so q is matched literally (backslash is the default escape)."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _raise_duplicate_sku(e: psycopg.errors.UniqueViolation, sku: str | None):
    if e.diag.constraint_name != "parts_sku_key":
        raise e
    raise api_error(
        409, "duplicate_sku", "a part with this sku already exists", details={"sku": sku}
    ) from e


@router.get("/parts")
def list_parts(
    q: str | None = None,
    sort: str = "created_at:desc",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    order_sql = _parse_sort(sort)

    clauses: list[str] = []
    where_params: list = []
    if q:
        like = _like_pattern(q)
        clauses.append("(sku ILIKE %s OR name ILIKE %s)")
        where_params.extend([like, like])
    where_sql = " AND ".join(clauses) if clauses else "true"

    sql = (
        f"SELECT *, count(*) over() as total FROM parts "
        f"WHERE {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s"
    )

    with pool().connection() as conn:
        rows = conn.execute(sql, [*where_params, limit, offset]).fetchall()
        if rows:
            total = rows[0]["total"]
        else:
            total = conn.execute(
                f"SELECT count(*) AS total FROM parts WHERE {where_sql}", where_params
            ).fetchone()["total"]

    items = [{k: v for k, v in row.items() if k != "total"} for row in rows]
    return {"items": items, "total": total}


@router.post("/parts", status_code=201, dependencies=[Depends(rate_limited(100, scope="writes"))])
def create_part(body: PartIn):
    sql = (
        "INSERT INTO parts (sku, name, price_cents, qty_on_hand) "
        "VALUES (%s, %s, %s, %s) RETURNING *"
    )
    params = [body.sku, body.name, body.price_cents, body.qty_on_hand]
    try:
        with pool().connection() as conn:
            row = conn.execute(sql, params).fetchone()
    except psycopg.errors.UniqueViolation as e:
        _raise_duplicate_sku(e, body.sku)
    return row


@router.get("/parts/{part_id}")
def get_part(part_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute("SELECT * FROM parts WHERE id = %s", [part_id]).fetchone()
    if row is None:
        raise api_error(404, "not_found", "part not found")
    return row


@router.patch("/parts/{part_id}", dependencies=[Depends(rate_limited(100, scope="writes"))])
def patch_part(part_id: uuid.UUID, body: PartPatch):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return get_part(part_id)

    set_sql = ", ".join(f"{col} = %s" for col in fields)
    params = [*fields.values(), part_id]
    sql = f"UPDATE parts SET {set_sql} WHERE id = %s RETURNING *"

    try:
        with pool().connection() as conn:
            row = conn.execute(sql, params).fetchone()
    except psycopg.errors.UniqueViolation as e:
        _raise_duplicate_sku(e, fields.get("sku"))
    if row is None:
        raise api_error(404, "not_found", "part not found")
    return row


@router.delete(
    "/parts/{part_id}", status_code=204, dependencies=[Depends(rate_limited(100, scope="writes"))]
)
def delete_part(part_id: uuid.UUID):
    with pool().connection() as conn:
        row = conn.execute("DELETE FROM parts WHERE id = %s RETURNING id", [part_id]).fetchone()
    if row is None:
        raise api_error(404, "not_found", "part not found")
```

**Status/lifecycle fields:** if `<Module>` has a field like `vehicles.status`
that moves through a fixed set of states, do NOT put it in `<Module>In`'s
free-form update path or `<Module>Patch` at all. Instead: define an
`ALLOWED: dict[str, set[str]]` transition matrix (see
`api/_lib/transitions.py`), add a `Status` `Literal[...]` type, a
`StatusIn` model with just the target `status`, and a
`POST /<module>/{id}/status` route that reads the current row under
`SELECT ... FOR UPDATE` inside `conn.transaction()`, checks
`can_transition(current, new)`, raises `api_error(422, "illegal_transition",
...)` on a bad move, and otherwise updates and returns the row — copy
`set_vehicle_status` in `api/_lib/vehicles.py` verbatim, renaming.

## Step 3 — wire into `api/index.py`

```python
from ._lib.parts import router as parts_router
...
app.include_router(parts_router, prefix="/api")
```

## Step 4 — web page

Add the TS type and verb calls to `web/src/lib/api.ts` (next to `Vehicle`):

```ts
export interface Part {
  id: string
  sku: string
  name: string
  price_cents: number
  qty_on_hand: number
  created_at: string
  updated_at: string
}
```

A list hook modeled on `web/src/hooks/useVehicleList.ts` — same shape, swap
the entity, endpoint, and sort whitelist:

```ts
export const SORT_FIELDS = ['created_at', 'price_cents', 'qty_on_hand'] as const
// ...same useEffect/AbortController pattern as useVehicleList, calling
// api.get<ListResponse<Part>>('/api/parts', { q, sort, limit, offset }, signal)
```

A page component modeled on `web/src/pages/InventoryPage.tsx`: an Antd
`Table<Part>` with server-driven search/sort/pagination, a "New part"
button opening a create/edit `Drawer` (model the drawer directly on
`web/src/components/VehicleFormDrawer.tsx` — same `diffPatch`-on-edit,
`ApiError` handling for `duplicate_sku` surfaced on the `sku` field). Render
`price_cents` through the same `Intl.NumberFormat` currency formatter,
divided by 100 for display and multiplied back by 100 on submit — never
carry a float through the API boundary.

Wire the route into `web/src/App.tsx`, nested under `<AuthGuard>` next to
the existing `index` route:

```tsx
<Route path="/" element={<AuthGuard />}>
  <Route index element={<InventoryPage />} />
  <Route path="parts" element={<PartsPage />} />
</Route>
```

## Step 5 — tests (`tests/test_parts_api.py`)

Model directly on `tests/test_vehicles_api.py`: the 2-line `_clean`
`autouse` wrapper around the shared `clean` fixture, a `unique_sku()` +
`part_body(**overrides)` factory, a `create_part` helper, the
401-on-every-route test enumerating every route, happy-path tests for each
verb, parametrized 422 cases, a duplicate-sku 409 test, sort/whitelist
tests (including the unknown-sort-field-returns-422 case), and pagination
tests.

```python
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean(clean):
    yield


def unique_sku() -> str:
    return uuid.uuid4().hex[:12].upper()


def part_body(**overrides) -> dict:
    body = {"sku": unique_sku(), "name": "Brake Pad Set", "price_cents": 4500, "qty_on_hand": 10}
    body.update(overrides)
    return body


def create_part(db_client: TestClient, auth_headers: dict, **overrides) -> dict:
    r = db_client.post("/api/parts", json=part_body(**overrides), headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_401_without_token_on_every_route(db_client: TestClient):
    pid = "00000000-0000-0000-0000-000000000000"
    requests = [
        ("GET", "/api/parts", None),
        ("POST", "/api/parts", part_body()),
        ("GET", f"/api/parts/{pid}", None),
        ("PATCH", f"/api/parts/{pid}", {"qty_on_hand": 1}),
        ("DELETE", f"/api/parts/{pid}", None),
    ]
    for method, path, body in requests:
        r = db_client.request(method, path, json=body)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
        assert r.json()["code"] == "unauthenticated"


def test_create_part_returns_201_full_row(db_client: TestClient, auth_headers: dict):
    body = part_body()
    r = db_client.post("/api/parts", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    row = r.json()
    assert row["sku"] == body["sku"]
    assert isinstance(row["price_cents"], int)


def test_create_duplicate_sku_returns_409(db_client: TestClient, auth_headers: dict):
    sku = unique_sku()
    create_part(db_client, auth_headers, sku=sku)
    r = db_client.post("/api/parts", json=part_body(sku=sku), headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["code"] == "duplicate_sku"


@pytest.mark.parametrize("overrides", [{"sku": ""}, {"price_cents": -1}, {"qty_on_hand": -1}])
def test_create_invalid_body_returns_422(
    db_client: TestClient, auth_headers: dict, overrides: dict
):
    r = db_client.post("/api/parts", json=part_body(**overrides), headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_list_sort_unknown_field_returns_422(db_client: TestClient, auth_headers: dict):
    create_part(db_client, auth_headers)
    r = db_client.get("/api/parts", params={"sort": "evil:asc"}, headers=auth_headers)
    assert r.status_code == 422


# ... continue with get/patch/delete happy-path + 404 + malformed-uuid 422
# tests, mirroring test_vehicles_api.py's structure section by section.
```

## Step 6 — gate check before done

Run the full gate before declaring the module done:

```bash
just gate
```

This runs `ruff check`, `ruff format --check`, `pytest` (your new test file
included), `npm --prefix web run lint`, `npm --prefix web run typecheck`,
and `npm --prefix web run build`. All six must be green. If you're driving
this inside a rails agent session, `just gate` is exactly what the loop
re-checks after your session ends — don't skip it locally and hope.

Once the gate is green, consider running the `domain-reviewer` skill's
checklist against your own diff before handing it off — it catches the same
things a cross-vendor reviewer would flag (missing auth, floats for money,
an unwhitelisted sort column, a PATCH that bypasses a status endpoint,
missing 401/422 test coverage).
