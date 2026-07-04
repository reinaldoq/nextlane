"""Tests for rails.agents.migrate: the migrate day-2 agent.

`run_agent_task` itself is fully covered by test_loop.py's fakes -- here we
only verify migrate() composes the right task_kind/task_body/title and
plumbs engine/reviewer/open_pr through to it. Unlike build_feature (which
calls `run_agent_task` as a bare module global, monkeypatchable at test
time), migrate's `run_fn` is an explicit injected parameter (mirrors
triage's fetch_fn/mark_fn/run_fn) -- so tests pass a recording fake via
`run_fn=` directly rather than monkeypatching the module attribute (which
would NOT affect an already-bound default parameter value).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rails.agents.migrate import migrate
from rails.config import RailsConfig


def make_config(**over) -> RailsConfig:
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path("/repo")}
    defaults.update(over)
    return RailsConfig(**defaults)


@pytest.fixture
def fake_run_agent_task():
    calls = []

    def _fake(cfg, **kwargs):
        calls.append({"cfg": cfg, **kwargs})
        return "sentinel-run-record"

    return calls, _fake


# --- task_kind / task_body ------------------------------------------------


def test_migrate_sets_task_kind_migrate(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index on vehicles.vin", run_fn=run_fn)

    assert calls[0]["task_kind"] == "migrate"


def test_migrate_body_mentions_supabase_migration_new(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index on vehicles.vin", run_fn=run_fn)

    body = calls[0]["task_body"]
    assert "supabase migration new" in body


def test_migrate_body_mentions_db_reset(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index on vehicles.vin", run_fn=run_fn)

    body = calls[0]["task_body"]
    assert "supabase db reset" in body


def test_migrate_body_mentions_agents_md(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index on vehicles.vin", run_fn=run_fn)

    body = calls[0]["task_body"]
    assert "AGENTS.md" in body


def test_migrate_body_includes_the_change_text(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add a NOT NULL constraint to vehicles.vin", run_fn=run_fn)

    body = calls[0]["task_body"]
    assert "add a NOT NULL constraint to vehicles.vin" in body


# --- title -----------------------------------------------------------------


def test_migrate_title_is_feat_schema_scoped_change(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index on vehicles.vin", run_fn=run_fn)

    assert calls[0]["title"] == "feat(schema): add an index on vehicles.vin"


def test_migrate_title_truncates_to_about_50_chars(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()
    long_change = (
        "Add a very long schema change description that goes on and on past fifty characters"
    )

    migrate(cfg, long_change, run_fn=run_fn)

    title = calls[0]["title"]
    assert title.startswith("feat(schema): ")
    assert len(title) <= len("feat(schema): ") + 50


# --- pass-through of engine/reviewer/open_pr -------------------------------


def test_migrate_passes_engine_and_reviewer_and_open_pr(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index", engine="codex", reviewer="claude", open_pr=False, run_fn=run_fn)

    call = calls[0]
    assert call["engine"] == "codex"
    assert call["reviewer_engine"] == "claude"
    assert call["open_pr"] is False


def test_migrate_defaults_engine_reviewer_none_open_pr_true(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config()

    migrate(cfg, "add an index", run_fn=run_fn)

    call = calls[0]
    assert call["engine"] is None
    assert call["reviewer_engine"] is None
    assert call["open_pr"] is True


def test_migrate_passes_cfg_through(fake_run_agent_task):
    calls, run_fn = fake_run_agent_task
    cfg = make_config(engine="gemini")

    migrate(cfg, "add an index", run_fn=run_fn)

    assert calls[0]["cfg"] is cfg


# --- return value ------------------------------------------------------------


def test_migrate_returns_run_agent_task_result(fake_run_agent_task):
    _calls, run_fn = fake_run_agent_task
    cfg = make_config()

    result = migrate(cfg, "add an index", run_fn=run_fn)

    assert result == "sentinel-run-record"


def test_migrate_defaults_run_fn_to_run_agent_task():
    """The public contract: an un-overridden call routes through the real
    `rails.agents.loop.run_agent_task` -- proven by identity of the bound
    default, not by exercising the real loop (which needs a real worktree)."""
    import rails.agents.loop as loop_mod
    from rails.agents.migrate import migrate as migrate_fn

    assert migrate_fn.__kwdefaults__["run_fn"] is loop_mod.run_agent_task
