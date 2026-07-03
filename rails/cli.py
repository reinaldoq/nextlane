"""Typer CLI skeleton for the rails runner.

Task 1 scope: the full command surface (build-feature, triage, migrate,
review, gate, engines) plus a working `engines` command. The other commands
are stubs -- they raise typer.Exit(1) after printing "not implemented" --
until their owning tasks (2-6) wire them to the real orchestration loop.
`gate` is deliberately stubbed here too: the plan wires `rails gate` in a
later task even though the underlying gate runner lands in Task 4.
"""

from __future__ import annotations

import shutil

import typer
from rich.console import Console

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
    task: str = typer.Argument(..., help="Plain-language description of the feature to build."),
) -> None:
    """Drive a headless agent session to implement a feature end-to-end."""
    _not_implemented()


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
    """Run the deterministic gate (lint/test/build) standalone."""
    _not_implemented()


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
