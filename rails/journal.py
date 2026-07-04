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

Portability (Task 10): `transcript_paths` are absolute local paths as built
by the loop (`<repo_root>/.worktrees/<slug>/.rails-transcripts/...`) -- fine
in memory for an operator tailing a live session, but a committed journal
line with an absolute path leaks a local username and isn't portable.
`record()` relativizes each transcript path to `repo_root` (or its
git-derived default) at write time, leaving the in-memory `RunRecord`
untouched; `from_row`/`load()` don't care either way, since they just parse
whatever string is on disk (old, pre-Task-10 lines stay absolute and still
load fine).
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: The only valid values for `RunRecord.outcome`.
#: "completed_no_pr" (Task 6): the loop's `open_pr=False` path -- gate went
#: green but the caller asked to stop short of opening a PR (used for
#: inspection runs); distinct from "pr_opened" (gate green AND a PR exists)
#: and from "gate_failed"/"error" (something went wrong).
#: "no_changes" (Task 6): the gate went green but the agent left ZERO commits
#: on the branch -- nothing to review or open a PR for.
#: "timeout" (Task 6): the whole-run wall-clock budget was exhausted before
#: the run finished.
VALID_OUTCOMES = frozenset(
    {
        "pr_opened",
        "gate_failed",
        "review_rejected",
        "error",
        "completed_no_pr",
        "no_changes",
        "timeout",
    }
)


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
    about. Bumped to 2 in Task 6 for `transcript_paths` / `review_verdict`.
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
    # Task 6 additions -- both carry defaults (old rows lack them; from_row
    # fills the field default / default_factory for whichever is missing).
    transcript_paths: list[str] = field(default_factory=list)
    review_verdict: str | None = None
    schema_version: int = 2

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
        -- the field's declared default (e.g. `schema_version`) or
        default_factory (e.g. `transcript_paths`) if it has one, otherwise
        None. That lets old lines (missing later-added fields) and newer
        lines (carrying fields this code doesn't know) both load.
        """
        kwargs: dict = {}
        for f in dataclasses.fields(cls):
            if f.name in row:
                kwargs[f.name] = row[f.name]
            elif f.default is not dataclasses.MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                kwargs[f.name] = f.default_factory()
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
        transcript_paths: list[str] | None = None,
        review_verdict: str | None = None,
    ) -> RunRecord:
        """Build a RunRecord, stamping `ts_iso` with the current UTC time.

        The sole call site for `datetime.now(UTC)` in this module -- build
        a RunRecord any other way (the dataclass constructor directly) to
        supply your own `ts_iso`, e.g. deterministically in a test. Task 6's
        loop builds RunRecords via the constructor directly instead (it
        injects its own `now_fn`), so this classmethod stays the convenient
        entry point for callers (and tests) that don't need to fake the
        clock.
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
            transcript_paths=transcript_paths if transcript_paths is not None else [],
            review_verdict=review_verdict,
        )


def _default_repo_root() -> Path:
    # Resolved lazily (only when a caller doesn't pass repo_root) so
    # importing this module never shells out to git -- see RailsConfig.load.
    from rails.config import RailsConfig

    return RailsConfig.load().repo_root


def _default_journal_path() -> Path:
    return _default_repo_root() / "rails" / "journal" / "runs.jsonl"


def _relativize_transcript_path(path_str: str, repo_root: Path) -> str:
    """Rewrite `path_str` relative to `repo_root` when possible.

    A path already relative, or an absolute path that doesn't live under
    `repo_root` (e.g. a fixture path or a transcript from outside this
    checkout), is returned unchanged -- this is a best-effort portability
    scrub for committed evidence (Task 10), not a strict contract.
    """
    path = Path(path_str)
    if not path.is_absolute():
        return path_str
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return path_str


def record(
    run: RunRecord, *, journal_path: Path | None = None, repo_root: Path | None = None
) -> None:
    """Append `run` as one JSON line to `journal_path`.

    Creates the parent directory if it doesn't exist yet. Defaults to
    `<repo_root>/rails/journal/runs.jsonl` when `journal_path` is omitted.

    `transcript_paths` are written repo-root-relative (see
    `_relativize_transcript_path`) so a committed journal never leaks an
    absolute local path (e.g. a username in `/Users/<name>/...`) -- the
    RunRecord object `run` itself is never mutated, only the JSON line
    written to disk. `repo_root` defaults (lazily, only when there's at
    least one transcript path to relativize) to the same git-derived root
    the rest of rails uses (`RailsConfig.load().repo_root`); pass it
    explicitly for a deterministic, git-free test.
    """
    path = journal_path if journal_path is not None else _default_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dataclasses.asdict(run)
    if row["transcript_paths"]:
        root = repo_root if repo_root is not None else _default_repo_root()
        row["transcript_paths"] = [
            _relativize_transcript_path(p, root) for p in row["transcript_paths"]
        ]
    with path.open("a", encoding="utf-8") as fh:
        # ensure_ascii=False: the journal is committed as human-readable
        # evidence (Task 9) -- accents/emoji in a task_summary or PR title
        # stay legible instead of turning into \uXXXX escapes.
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


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
