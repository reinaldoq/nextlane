from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from .db import pool
from .ratelimit import rate_limited

router = APIRouter()

Kind = Literal["bug_report", "client_error"]


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    message: str = Field(min_length=1, max_length=4000)
    context: dict = Field(default_factory=dict)


# No GET endpoint here (YAGNI) -- the Phase 2 triage agent reads app_events
# directly from the DB, so there's no reader to build a list/detail API for yet.
@router.post("/events", status_code=201, dependencies=[Depends(rate_limited(30))])
def create_event(body: EventIn):
    sql = "INSERT INTO app_events (kind, message, context) VALUES (%s, %s, %s) RETURNING *"
    with pool().connection() as conn:
        row = conn.execute(sql, [body.kind, body.message, Jsonb(body.context)]).fetchone()
    return row
