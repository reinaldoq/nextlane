"""Typer CLI skeleton for the rails runner.

Task 1 scope: the full command surface (build-feature, triage, migrate,
review, gate, engines) plus a working `engines` command. `gate` was wired in
Task 4 once rails/gate.py landed. `build-feature` was wired in Task 6 onto
the shared agent loop. The remaining commands (triage, migrate, review) stay
stubs -- they raise typer.Exit(1) after printing "not implemented" -- until
Task 8 wires them to the same orchestration loop.
"""

from __future__ import annotations

import shutil

import typer
from rich.console import Console

from rails.agents.build_feature import build_feature as _build_feature
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


def _not_implemented() -> None:
    err_console.print("not implemented — arrives in a later task")
    raise typer.Exit(1)


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
) -> None:
    """Drive a headless agent session to implement a feature end-to-end."""
    cfg = RailsConfig.load()
    _build_feature(cfg, spec, engine=engine, reviewer=reviewer, open_pr=not no_pr)


@app.command()
def triage(
    window: str = typer.Argument(..., help="Time window or query describing what to triage."),
) -> None:
    """Drive a headless agent session to triage recent app_events."""
    _not_implemented()


@app.command()
def migrate(
    task: str = typer.Argument(..., help="Plain-language description of the migration."),
) -> None:
    """Drive a headless agent session to author and apply a migration."""
    _not_implemented()


@app.command()
def review(
    pr: str = typer.Option(..., "--pr", help="PR number or URL to review."),
) -> None:
    """Drive a cross-vendor review session against an open PR."""
    _not_implemented()


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
