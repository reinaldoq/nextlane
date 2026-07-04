"""Day-2 agents built on top of `rails.agents.loop.run_agent_task`.

Spec ref: Phase-2 Task 6 (`build_feature`) / Task 8 (`triage`, `migrate`,
`review`, still stubbed in `rails/cli.py` as of Task 6). Each module here is a
thin, task-specific wrapper: it builds a `task_kind` / `task_body` / `title`
and calls `run_agent_task`, which owns the actual worktree/gate/review/PR
orchestration.
"""

from __future__ import annotations
