"""Tests for rails.journal: the append-only JSONL run journal.

Every run of the agent loop (Task 6) appends one `RunRecord` here as
evidence -- this module only needs to get the read/write round-trip exactly
right; Task 6 owns filling in real values.
"""

from __future__ import annotations

import dataclasses
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
    for outcome in (
        "pr_opened",
        "gate_failed",
        "review_rejected",
        "error",
        "completed_no_pr",
        "no_changes",
        "timeout",
    ):
        run = RunRecord.new(**_make_kwargs(outcome=outcome))
        assert run.outcome == outcome


def test_run_record_is_frozen():
    run = RunRecord.new(**_make_kwargs())
    with pytest.raises(Exception):  # noqa: B017 -- dataclasses raise FrozenInstanceError
        run.outcome = "error"


def test_new_stamps_schema_version():
    run = RunRecord.new(**_make_kwargs())
    assert run.schema_version == 2


def test_new_defaults_transcript_paths_and_review_verdict():
    run = RunRecord.new(**_make_kwargs())
    assert run.transcript_paths == []
    assert run.review_verdict is None


def test_new_accepts_transcript_paths_and_review_verdict():
    run = RunRecord.new(
        **_make_kwargs(
            transcript_paths=["/tmp/a.jsonl", "/tmp/b.jsonl"],
            review_verdict="APPROVE",
        )
    )
    assert run.transcript_paths == ["/tmp/a.jsonl", "/tmp/b.jsonl"]
    assert run.review_verdict == "APPROVE"


# --- from_row schema tolerance ----------------------------------------
#
# Phase 3 will add fields to the journal while old lines persist in the same
# file. from_row must (a) fill a default for a field missing from an OLD line
# and (b) ignore an UNKNOWN key present in a NEWER line -- so old and new code
# can read each other's rows.


def test_from_row_fills_missing_schema_version_with_default():
    run = RunRecord.new(**_make_kwargs())
    row = dataclasses.asdict(run)
    del row["schema_version"]  # an "old" line, written before schema_version existed

    restored = RunRecord.from_row(row)

    assert restored.schema_version == 2
    assert restored == run  # the default makes it round-trip-equal


def test_from_row_fills_missing_transcript_paths_via_default_factory():
    """transcript_paths uses `field(default_factory=list)`, not a plain
    `default=` -- from_row must call the factory (producing a fresh empty
    list), not just fall through to None, for a pre-Task-6 line that lacks
    the field entirely."""
    run = RunRecord.new(**_make_kwargs())
    row = dataclasses.asdict(run)
    del row["transcript_paths"]

    restored = RunRecord.from_row(row)

    assert restored.transcript_paths == []
    assert restored == run


def test_from_row_fills_missing_review_verdict_with_none():
    run = RunRecord.new(**_make_kwargs())
    row = dataclasses.asdict(run)
    del row["review_verdict"]

    restored = RunRecord.from_row(row)

    assert restored.review_verdict is None


def test_from_row_fills_missing_optional_field_with_none():
    run = RunRecord.new(**_make_kwargs())
    row = dataclasses.asdict(run)
    del row["pr_url"]  # a future/older line missing an optional field

    restored = RunRecord.from_row(row)

    assert restored.pr_url is None


def test_from_row_ignores_unknown_extra_keys():
    run = RunRecord.new(**_make_kwargs())
    row = dataclasses.asdict(run)
    row["field_added_in_phase3"] = "whatever"  # a newer line, unknown to us

    restored = RunRecord.from_row(row)

    assert restored == run  # extra key ignored, no crash


def test_from_row_round_trips_asdict():
    run = RunRecord.new(**_make_kwargs())

    assert RunRecord.from_row(dataclasses.asdict(run)) == run


def test_load_tolerates_old_and_future_schema_lines(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    old = dataclasses.asdict(RunRecord.new(**_make_kwargs(task_summary="old line")))
    del old["schema_version"]
    future = dataclasses.asdict(RunRecord.new(**_make_kwargs(task_summary="future line")))
    future["phase3_field"] = 123
    journal_path.write_text(json.dumps(old) + "\n" + json.dumps(future) + "\n", encoding="utf-8")

    loaded = load(journal_path)

    assert len(loaded) == 2
    assert loaded[0].task_summary == "old line"
    assert loaded[0].schema_version == 2
    assert loaded[1].task_summary == "future line"


def test_record_writes_utf8_without_ascii_escaping(tmp_path):
    journal_path = tmp_path / "runs.jsonl"
    run = RunRecord.new(**_make_kwargs(task_summary="Añadir estadísticas 🚗"))

    record(run, journal_path=journal_path)

    raw = journal_path.read_text(encoding="utf-8")
    # human-readable: the accented/emoji chars survive verbatim, not as \uXXXX
    assert "Añadir estadísticas 🚗" in raw
    assert "\\u" not in raw
    assert load(journal_path)[0].task_summary == "Añadir estadísticas 🚗"


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
