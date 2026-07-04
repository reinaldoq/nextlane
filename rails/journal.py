"""Append-only run journal: one JSON line per agent-driven task run.

Spec ref: Phase-2 Task 5 (stub) / Task 6 (real writer) / Task 9 (first real
entries committed as evidence). `rails/journal/runs.jsonl` is the durable
record Task 6's loop appends to after every run (PR opened, gate failed,
review rejected, or errored) and Task 10's summary / Phase 3 read back.

The directory `rails/journal/` is tracked in git (via `.gitkeep`) so it
exists in a fresh checkout; `runs.jsonl` itself is NOT gitignored -- real
run records are meant to be committed later as evidence the rails actually
ran (see Task 9), so nothing here should exclude it.

Timestamp discipline: `datetime.now(UTC)` is called in exactly ONE place,
`RunRecord.new`, so every record's clock read happens the same way and
tests can stamp their own `ts_iso` deterministically without touching the
wall clock at all.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: The only valid values for `RunRecord.outcome`.
VALID_OUTCOMES = frozenset({"pr_opened", "gate_failed", "review_rejected", "error"})


@dataclass(frozen=True)
class RunRecord:
    """One row of the run journal -- see module docstring.

    `schema_version` distinguishes journal-line layouts across phases. It is
    the LAST field and carries a default so that:
      - old lines written before it existed still parse (`from_row` fills the
        default), and
      - fields added in a future phase can likewise be declared with defaults
        so both old and new code read each other's rows.
    Bump it whenever the set of fields changes in a way readers must know
    about.
    """

    ts_iso: str
    task_kind: str
    task_summary: str
    engine: str
    reviewer_engine: str | None
    worktree_branch: str
    gate_ok: bool
    retries: int
    duration_s: float
    cost_usd: float | None
    pr_url: str | None
    outcome: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"RunRecord.outcome must be one of {sorted(VALID_OUTCOMES)}, got {self.outcome!r}"
            )

    @classmethod
    def from_row(cls, row: dict) -> RunRecord:
        """Build a RunRecord from a raw JSON dict, tolerant of schema drift.

        Selects only KNOWN fields (unknown keys from a newer writer are
        ignored) and fills a default for any known field MISSING from the row
        -- the field's declared default if it has one (e.g. schema_version),
        otherwise None. That lets old lines (missing later-added fields) and
        newer lines (carrying fields this code doesn't know) both load.
        """
        kwargs: dict = {}
        for f in dataclasses.fields(cls):
            if f.name in row:
                kwargs[f.name] = row[f.name]
            elif f.default is not dataclasses.MISSING:
                kwargs[f.name] = f.default
            else:
                kwargs[f.name] = None
        return cls(**kwargs)

    @classmethod
    def new(
        cls,
        *,
        task_kind: str,
        task_summary: str,
        engine: str,
        reviewer_engine: str | None,
        worktree_branch: str,
        gate_ok: bool,
        retries: int,
        duration_s: float,
        cost_usd: float | None,
        pr_url: str | None,
        outcome: str,
    ) -> RunRecord:
        """Build a RunRecord, stamping `ts_iso` with the current UTC time.

        The sole call site for `datetime.now(UTC)` in this module -- build
        a RunRecord any other way (the dataclass constructor directly) to
        supply your own `ts_iso`, e.g. deterministically in a test.
        """
        return cls(
            ts_iso=datetime.now(UTC).isoformat(),
            task_kind=task_kind,
            task_summary=task_summary,
            engine=engine,
            reviewer_engine=reviewer_engine,
            worktree_branch=worktree_branch,
            gate_ok=gate_ok,
            retries=retries,
            duration_s=duration_s,
            cost_usd=cost_usd,
            pr_url=pr_url,
            outcome=outcome,
        )


def _default_journal_path() -> Path:
    # Resolved lazily (only when a caller doesn't pass journal_path) so
    # importing this module never shells out to git -- see RailsConfig.load.
    from rails.config import RailsConfig

    return RailsConfig.load().repo_root / "rails" / "journal" / "runs.jsonl"


def record(run: RunRecord, *, journal_path: Path | None = None) -> None:
    """Append `run` as one JSON line to `journal_path`.

    Creates the parent directory if it doesn't exist yet. Defaults to
    `<repo_root>/rails/journal/runs.jsonl` when `journal_path` is omitted.
    """
    path = journal_path if journal_path is not None else _default_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        # ensure_ascii=False: the journal is committed as human-readable
        # evidence (Task 9) -- accents/emoji in a task_summary or PR title
        # stay legible instead of turning into \uXXXX escapes.
        fh.write(json.dumps(dataclasses.asdict(run), ensure_ascii=False) + "\n")


def load(journal_path: Path | None = None) -> list[RunRecord]:
    """Parse `journal_path` back into `RunRecord`s, in append order.

    A missing file is tolerated and yields an empty list -- a fresh repo
    (or a fresh journal directory before the first run) has no journal yet.
    """
    path = journal_path if journal_path is not None else _default_journal_path()
    if not path.exists():
        return []
    records: list[RunRecord] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(RunRecord.from_row(json.loads(line)))
    return records
