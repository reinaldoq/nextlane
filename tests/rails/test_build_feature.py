"""Tests for rails.agents.build_feature: the build-feature agent wrapper.

`run_agent_task` itself is fully covered by test_loop.py's fakes; here we
only need to verify build_feature composes the right task_kind/task_body/
title and plumbs engine/reviewer/open_pr through to it -- so
`run_agent_task` itself is monkeypatched to a recording fake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rails.agents.build_feature as build_feature_mod
from rails.agents.build_feature import build_feature
from rails.config import RailsConfig


def make_config(**over) -> RailsConfig:
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path("/repo")}
    defaults.update(over)
    return RailsConfig(**defaults)


@pytest.fixture
def fake_run_agent_task(monkeypatch):
    calls = []

    def _fake(cfg, **kwargs):
        calls.append({"cfg": cfg, **kwargs})
        return "sentinel-run-record"

    monkeypatch.setattr(build_feature_mod, "run_agent_task", _fake)
    return calls


# --- task_kind / task_body ------------------------------------------------


def test_build_feature_sets_task_kind_feature(fake_run_agent_task):
    cfg = make_config()

    build_feature(cfg, "Add a widget")

    assert fake_run_agent_task[0]["task_kind"] == "feature"


def test_build_feature_appends_module_pointer_to_spec(fake_run_agent_task):
    cfg = make_config()

    build_feature(cfg, "Add a widget")

    body = fake_run_agent_task[0]["task_body"]
    assert body.startswith("Add a widget")
    assert "AGENTS.md" in body
    assert "vehicles" in body


# --- title -----------------------------------------------------------------


def test_build_feature_title_is_rails_run_scoped_spec(fake_run_agent_task):
    cfg = make_config()

    build_feature(cfg, "Add a GET /api/vehicles/stats endpoint")

    assert (
        fake_run_agent_task[0]["title"] == "feat(rails-run): Add a GET /api/vehicles/stats endpoint"
    )


def test_build_feature_title_truncates_to_about_55_chars(fake_run_agent_task):
    cfg = make_config()
    long_spec = (
        "Add a very long feature description that goes on and on past sixty characters for sure"
    )

    build_feature(cfg, long_spec)

    title = fake_run_agent_task[0]["title"]
    assert title.startswith("feat(rails-run): ")
    assert len(title) <= len("feat(rails-run): ") + 55


def test_build_feature_title_collapses_newlines_and_whitespace(fake_run_agent_task):
    cfg = make_config()
    spec = "Add a widget\nwith   multiple\n\nlines of   spacing"

    build_feature(cfg, spec)

    title = fake_run_agent_task[0]["title"]
    assert "\n" not in title
    assert "  " not in title


# --- pass-through of engine/reviewer/open_pr -------------------------------


def test_build_feature_passes_engine_and_reviewer_and_open_pr(fake_run_agent_task):
    cfg = make_config()

    build_feature(cfg, "Add a widget", engine="codex", reviewer="claude", open_pr=False)

    call = fake_run_agent_task[0]
    assert call["engine"] == "codex"
    assert call["reviewer_engine"] == "claude"
    assert call["open_pr"] is False


def test_build_feature_defaults_engine_reviewer_none_open_pr_true(fake_run_agent_task):
    cfg = make_config()

    build_feature(cfg, "Add a widget")

    call = fake_run_agent_task[0]
    assert call["engine"] is None
    assert call["reviewer_engine"] is None
    assert call["open_pr"] is True


def test_build_feature_passes_cfg_through(fake_run_agent_task):
    cfg = make_config(engine="gemini")

    build_feature(cfg, "Add a widget")

    assert fake_run_agent_task[0]["cfg"] is cfg


# --- return value ------------------------------------------------------------


def test_build_feature_returns_run_agent_task_result(fake_run_agent_task):
    cfg = make_config()

    result = build_feature(cfg, "Add a widget")

    assert result == "sentinel-run-record"
