"""Tests for rails.journal: the append-only JSONL run journal.

Every run of the agent loop (Task 6) appends one `RunRecord` here as
evidence -- this module only needs to get the read/write round-trip exactly
right; Task 6 owns filling in real values.
"""

from __future__ import annotations

import json

import pytest

from rails.journal import RunRecord, load, record

# --- RunRecord.new -----------------------------------------------------


def _make_kwargs(**overrides):
    kwargs = dict(
        task_kind="build-feature",
        task_summary="Add vehicle stats endpoint",
        engine="claude",
        reviewer_engine="codex",
        worktree_branch="rails/build-feature-abc123",
        gate_ok=True,
        retries=0,
        duration_s=42.5,
        cost_usd=0.37,
        pr_url="https://github.com/reinaldoq/nextlane/pull/42",
        outcome="pr_opened",
    )
    kwargs.update(overrides)
    return kwargs


def test_new_stamps_ts_iso():
    run = RunRecord.new(**_make_kwargs())

    assert isinstance(run.ts_iso, str)
    assert run.ts_iso  # non-empty
    # ISO 8601 with a timezone offset (datetime.now(UTC).isoformat() always
    # includes +00:00) -- a cheap sanity check without re-implementing
    # datetime parsing here.
    assert "+00:00" in run.ts_iso or run.ts_iso.endswith("Z")


def test_new_rejects_invalid_outcome():
    with pytest.raises(ValueError):
        RunRecord.new(**_make_kwargs(outcome="not_a_real_outcome"))


def test_new_accepts_all_documented_outcomes():
    for outcome in ("pr_opened", "gate_failed", "review_rejected", "error"):
        run = RunRecord.new(**_make_kwargs(outcome=outcome))
        assert run.outcome == outcome


def test_run_record_is_frozen():
    run = RunRecord.new(**_make_kwargs())
    with pytest.raises(Exception):  # noqa: B017 -- dataclasses raise FrozenInstanceError
        run.outcome = "error"


# --- record / load round-trip -----------------------------------------


def test_record_then_load_round_trips(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    run = RunRecord.new(**_make_kwargs())

    record(run, journal_path=journal_path)
    loaded = load(journal_path)

    assert loaded == [run]


def test_record_creates_parent_dir(tmp_path):
    journal_path = tmp_path / "nested" / "dir" / "runs.jsonl"
    run = RunRecord.new(**_make_kwargs())

    record(run, journal_path=journal_path)

    assert journal_path.exists()
    assert load(journal_path) == [run]


def test_multiple_appends_accumulate(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    run1 = RunRecord.new(**_make_kwargs(task_summary="first run"))
    run2 = RunRecord.new(**_make_kwargs(task_summary="second run", outcome="gate_failed"))

    record(run1, journal_path=journal_path)
    record(run2, journal_path=journal_path)
    loaded = load(journal_path)

    assert loaded == [run1, run2]


def test_load_missing_file_returns_empty_list(tmp_path):
    journal_path = tmp_path / "does-not-exist" / "runs.jsonl"

    assert load(journal_path) == []


def test_record_writes_one_json_line_per_call(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    run = RunRecord.new(**_make_kwargs())

    record(run, journal_path=journal_path)

    lines = journal_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["task_kind"] == "build-feature"
    assert parsed["outcome"] == "pr_opened"


def test_record_handles_none_fields(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    run = RunRecord.new(
        **_make_kwargs(reviewer_engine=None, cost_usd=None, pr_url=None, outcome="gate_failed")
    )

    record(run, journal_path=journal_path)
    loaded = load(journal_path)

    assert loaded == [run]
    assert loaded[0].reviewer_engine is None
    assert loaded[0].cost_usd is None
    assert loaded[0].pr_url is None
