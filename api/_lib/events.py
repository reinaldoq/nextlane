import json
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb

from .auth import current_user
from .db import pool
from .ratelimit import rate_limited

# Router-level auth is the canonical pattern for every business router; rate limiting is additive on writes.
router = APIRouter(dependencies=[Depends(current_user)])

Kind = Literal["bug_report", "client_error"]

MAX_CONTEXT_BYTES = 16_384

EVENTS_RATE_LIMIT = 30

# keep in sync with web/src/components/ReportIssueModal.tsx's MESSAGE_MAX_CHARS
MAX_MESSAGE_CHARS = 4000


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    context: dict = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def _cap_context_size(cls, v: dict) -> dict:
        # Cap serialized context: bounds jsonb row growth and the Phase-2
        # triage LLM's prompt cost / injection surface (context is untrusted
        # client input that gets fed to an agent).
        if len(json.dumps(v)) > MAX_CONTEXT_BYTES:
            raise ValueError("context too large")
        return v


# No GET endpoint here (YAGNI) -- the Phase 2 triage agent reads app_events
# directly from the DB, so there's no reader to build a list/detail API for yet.
@router.post(
    "/events",
    status_code=201,
    dependencies=[Depends(rate_limited(EVENTS_RATE_LIMIT, scope="events"))],
)
def create_event(body: EventIn):
    sql = "INSERT INTO app_events (kind, message, context) VALUES (%s, %s, %s) RETURNING *"
    with pool().connection() as conn:
        row = conn.execute(sql, [body.kind, body.message, Jsonb(body.context)]).fetchone()
    return row
