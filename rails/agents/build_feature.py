"""The build-feature agent: turn a plain-language feature spec into an
end-to-end change, driven through `rails.agents.loop.run_agent_task`.

Spec ref: Phase-2 Task 6.
"""

from __future__ import annotations

from rails.agents.loop import run_agent_task
from rails.config import RailsConfig
from rails.journal import RunRecord

# Points the agent at the reference module pattern (formalized in AGENTS.md
# by Task 7) so a new feature follows the same shape as an existing one
# instead of improvising its own conventions.
_MODULE_POINTER = (
    "\n\nFollow the module pattern in AGENTS.md (the `vehicles` module is the reference)."
)

_TITLE_MAX_LEN = 60


def _title_from_spec(spec: str, *, max_len: int = _TITLE_MAX_LEN) -> str:
    """`feat: <first ~max_len chars of spec>` -- whitespace (including
    newlines, for a multi-line spec) collapsed to single spaces first so the
    PR title/commit-adjacent title is always one clean line."""
    flattened = " ".join(spec.split())
    return f"feat: {flattened[:max_len].rstrip()}"


def build_feature(
    cfg: RailsConfig,
    spec: str,
    *,
    engine: str | None = None,
    reviewer: str | None = None,
    open_pr: bool = True,
) -> RunRecord:
    """Run the build-feature task end-to-end: `spec` (a plain-language
    description of the feature) becomes the task body, with a pointer to the
    `vehicles` reference module appended; `title` is derived from the first
    ~60 characters of `spec`. Delegates everything else -- worktree, gate,
    cross-vendor review, PR -- to `run_agent_task`."""
    return run_agent_task(
        cfg,
        task_kind="feature",
        task_body=spec + _MODULE_POINTER,
        title=_title_from_spec(spec),
        engine=engine,
        reviewer_engine=reviewer,
        open_pr=open_pr,
    )
