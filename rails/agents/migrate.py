"""The migrate agent: turn a plain-language schema change into a migration
+ the affected app code, driven through `rails.agents.loop.run_agent_task`.

Spec ref: Phase-2 Task 8.
"""

from __future__ import annotations

from rails.agents.loop import run_agent_task
from rails.config import RailsConfig
from rails.journal import RunRecord

_TITLE_MAX_LEN = 50

_TEMPLATE = (
    "Make the following schema change to the Nextlane DMS. Create a new migration with "
    "`supabase migration new <name>`, write the SQL following the conventions in AGENTS.md "
    "(RLS enable, checks, indexes, updated_at trigger with set search_path=''), update the "
    "affected Pydantic models / routers, and prove it applies with `supabase db reset` before "
    "running the gate.\n\nChange: "
)


def _title_from_change(change: str, *, max_len: int = _TITLE_MAX_LEN) -> str:
    """`feat(schema): <first ~max_len chars of change>` -- mirrors
    build_feature's `feat(rails-run): ...` title shape but scoped to
    `schema` so a migration PR is recognizable at a glance."""
    flattened = " ".join(change.split())
    return f"feat(schema): {flattened[:max_len].rstrip()}"


def migrate(
    cfg: RailsConfig,
    change: str,
    *,
    engine: str | None = None,
    reviewer: str | None = None,
    open_pr: bool = True,
    run_fn=run_agent_task,
) -> RunRecord:
    """Run the migrate task end-to-end: `change` (a plain-language
    description of the schema change) becomes the task body, appended to a
    template instructing the agent on the migration/gate procedure; `title`
    is derived from the first ~50 characters of `change`. Delegates
    everything else -- worktree, gate, cross-vendor review, PR -- to
    `run_fn` (`run_agent_task`)."""
    return run_fn(
        cfg,
        task_kind="migrate",
        task_body=_TEMPLATE + change,
        title=_title_from_change(change),
        engine=engine,
        reviewer_engine=reviewer,
        open_pr=open_pr,
    )
