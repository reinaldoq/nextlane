"""Day-2 agents built on top of `rails.agents.loop.run_agent_task`.

Spec ref: Phase-2 Task 6 (`build_feature`) / Task 8 (`triage`, `migrate`,
`review`). `build_feature`, `triage`, and `migrate` are thin, task-specific
wrappers: each builds a `task_kind` / `task_body` / `title` and calls
`run_agent_task`, which owns the actual worktree/gate/review/PR
orchestration. `review` is the one exception -- a standalone, read-only
reviewer that does not drive the loop at all (see `rails.agents.review`).
"""

from __future__ import annotations
