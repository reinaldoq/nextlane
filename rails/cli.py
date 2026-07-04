"""Typer CLI for the rails runner.

The full command surface: build-feature, triage, migrate, review, gate,
engines. Every day-2 agent command (build-feature, triage, migrate) shares
`rails.agents.loop.run_agent_task`; `review` is the one standalone,
read-only exception (see `rails.agents.review`). `gate` mirrors `just gate`
with structured per-step output.
"""

from __future__ import annotations

import shutil

import typer
from rich.console import Console

from rails.agents.build_feature import build_feature as _build_feature
from rails.agents.migrate import migrate as _migrate
from rails.agents.review import review as _review
from rails.agents.triage import triage as _triage
from rails.config import RailsConfig
from rails.gate import run_gate

app = typer.Typer(
    name="rails",
    help="Vendor-agnostic AI rails runner for the Nextlane DMS repo.",
    no_args_is_help=True,
)
# Errors and diagnostics go to stderr so stdout stays clean for piping.
err_console = Console(stderr=True)

ENGINES = ("claude", "codex", "gemini")


@app.command("build-feature")
def build_feature(
    spec: str = typer.Argument(..., help="Plain-language description of the feature to build."),
    engine: str = typer.Option(
        None, "--engine", help="Builder engine (claude|codex|gemini). Defaults to RAILS_ENGINE."
    ),
    reviewer: str = typer.Option(
        None,
        "--reviewer",
        help="Cross-vendor reviewer engine. Defaults to the other of claude/codex.",
    ),
    no_pr: bool = typer.Option(
        False,
        "--no-pr",
        help=(
            "Run the full loop but stop short of opening a PR. Leaves the worktree and its "
            "branch in place under .worktrees/ for inspection (they are NOT cleaned up)."
        ),
    ),
    retro: bool = typer.Option(
        True,
        "--retro/--no-retro",
        help=(
            "Self-improvement flywheel: after a PR opens, run one extra read-only session "
            "that proposes 0-3 generalizable lessons into the PR body for human review "
            "(never auto-written to rails/LEARNINGS.md). --no-retro skips it."
        ),
    ),
) -> None:
    """Drive a headless agent session to implement a feature end-to-end."""
    cfg = RailsConfig.load()
    _build_feature(cfg, spec, engine=engine, reviewer=reviewer, open_pr=not no_pr, retro=retro)


@app.command()
def triage(
    event: str = typer.Option(
        None, "--event", help="Specific app_events id to triage. Defaults to the newest new event."
    ),
    engine: str = typer.Option(
        None, "--engine", help="Builder engine (claude|codex|gemini). Defaults to RAILS_ENGINE."
    ),
    reviewer: str = typer.Option(
        None,
        "--reviewer",
        help="Cross-vendor reviewer engine. Defaults to the other of claude/codex.",
    ),
    no_pr: bool = typer.Option(
        False,
        "--no-pr",
        help=(
            "Run the full loop but stop short of opening a PR. Leaves the worktree and its "
            "branch in place under .worktrees/ for inspection (they are NOT cleaned up)."
        ),
    ),
) -> None:
    """Fetch a reported app_events row and drive a headless agent session
    to reproduce it with a failing test, then fix it."""
    cfg = RailsConfig.load()
    run = _triage(cfg, event_id=event, engine=engine, reviewer=reviewer, open_pr=not no_pr)
    if run is None:
        raise typer.Exit(1)


@app.command()
def migrate(
    change: str = typer.Argument(..., help="Plain-language description of the schema change."),
    engine: str = typer.Option(
        None, "--engine", help="Builder engine (claude|codex|gemini). Defaults to RAILS_ENGINE."
    ),
    reviewer: str = typer.Option(
        None,
        "--reviewer",
        help="Cross-vendor reviewer engine. Defaults to the other of claude/codex.",
    ),
    no_pr: bool = typer.Option(
        False,
        "--no-pr",
        help=(
            "Run the full loop but stop short of opening a PR. Leaves the worktree and its "
            "branch in place under .worktrees/ for inspection (they are NOT cleaned up)."
        ),
    ),
) -> None:
    """Drive a headless agent session to author and apply a schema migration."""
    cfg = RailsConfig.load()
    _migrate(cfg, change, engine=engine, reviewer=reviewer, open_pr=not no_pr)


@app.command()
def review(
    pr: str = typer.Option(None, "--pr", help="PR number or URL to review."),
    range_: str = typer.Option(
        None, "--range", help="A git diff range (e.g. main..my-branch) to review instead of a PR."
    ),
    engine: str = typer.Option(
        None, "--engine", help="Reviewer engine (claude|codex|gemini). Defaults to RAILS_ENGINE."
    ),
    comment: bool = typer.Option(
        False,
        "--comment",
        help="Post the verdict and reasoning back to the PR via `gh pr comment`.",
    ),
) -> None:
    """Run a standalone, read-only cross-vendor review against an open PR
    or an arbitrary diff range. Exits 0 on APPROVE, 1 on REQUEST_CHANGES --
    usable as a gate."""
    if pr is None and range_ is None:
        err_console.print("either --pr or --range is required")
        raise typer.Exit(1)
    cfg = RailsConfig.load()
    verdict = _review(cfg, pr=pr, diff_range=range_, engine=engine, comment=comment)
    raise typer.Exit(0 if verdict == "APPROVE" else 1)


@app.command()
def gate() -> None:
    """Run the deterministic gate (lint/test/build) standalone, in the
    current repo root -- a local mirror of `just gate`, but with the
    structured per-step summary rails' agent loop (Task 6) also consumes."""
    cfg = RailsConfig.load()
    result = run_gate(cfg.repo_root)
    typer.echo(result.summary())
    raise typer.Exit(0 if result.ok else 1)


@app.command()
def engines() -> None:
    """List supported engines and whether their CLI is available on PATH."""
    # typer.echo (not rich): pipe-safe — never wraps at terminal width, so
    # one engine is always exactly one line regardless of path length.
    width = max(len(name) for name in ENGINES)
    for name in ENGINES:
        path = shutil.which(name)
        marker = "available" if path else "missing"
        location = f" ({path})" if path else ""
        typer.echo(f"{name:<{width}}  {marker}{location}")


if __name__ == "__main__":
    app()
