"""`rails doctor`: a preflight PASS/FAIL report for everything a live rails
session needs, run BEFORE a demo/session instead of discovering a missing
Docker/Postgres, an unauthenticated `gh`, or an unset `.env` key partway
through a real `build-feature`/`triage` run.

Design: every check below is a small, **module-level, injectable** function
-- exactly the same seam pattern already used across `rails/` (`rails.gate`'s
subprocess steps, `rails.github`'s `runner`, `rails.events`'s `opener`):
`connect` for Postgres, `which` for engine lookup, `gh_runner` for `gh auth
status`, and plain data (`environ`/`env_file_text`) for the `.env` checks.
`run_doctor()` is the only function that touches the real filesystem/environ
by default; every check it composes can be unit-tested with a fake and zero
real Docker/gh/network, per Task spec (TDD, see tests/rails/test_doctor.py).

Only `claude`/`codex` (the demo's default builder/cross-vendor-reviewer
pair -- see AGENTS.md "budget discipline") and Postgres/gh-auth/env-keys/
migrations are CRITICAL: `gemini` is documented best-effort support
(AGENTS.md, README's engine table), so its absence is reported but never
fails the overall `rails doctor` exit code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

if TYPE_CHECKING:
    from rails.config import RailsConfig

ENGINES: tuple[str, ...] = ("claude", "codex", "gemini")

# The demo's default cross-vendor pair (`--engine claude --reviewer codex`,
# both directions proven in PR#18/#19/#23) -- missing either is CRITICAL.
# gemini is best-effort (AGENTS.md); its absence is informational only.
_CRITICAL_ENGINES = frozenset({"claude", "codex"})

# key -> which day-2 flow needs it, surfaced in the FAIL detail so a human
# knows exactly what to populate and why (see .env.example / AGENTS.md).
REQUIRED_ENV_KEYS: dict[str, str] = {
    "DATABASE_URL": "the gate (local Postgres, pytest step)",
    "SUPABASE_JWKS_URL": "the gate (API auth)",
    "SUPABASE_URL": "triage (fetch app_events)",
    "SUPABASE_SERVICE_ROLE_KEY": "triage (fetch app_events)",
}


@dataclass(frozen=True)
class CheckResult:
    """One named PASS/FAIL line. `critical=False` means: report it, but
    never fail `DoctorReport.ok`/the CLI's exit code over it (see module
    docstring re: gemini)."""

    name: str
    ok: bool
    detail: str
    critical: bool = True


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        """True iff every CRITICAL check passed. A failing non-critical
        check (e.g. `engine:gemini` missing) never flips this to False."""
        return all(check.ok for check in self.checks if check.critical)

    def summary(self) -> str:
        """One `PASS`/`FAIL  name  detail` line per check, in the order the
        checks ran; a failing non-critical check is tagged `(optional)` so
        it reads clearly as "reported, not blocking"."""
        width = max((len(check.name) for check in self.checks), default=0)
        lines = []
        for check in self.checks:
            status = "PASS" if check.ok else "FAIL"
            tag = "" if check.critical else " (optional)"
            lines.append(f"{status}  {check.name:<{width}}{tag}  {check.detail}")
        return "\n".join(lines)


def _parse_env_text(text: str) -> dict[str, str]:
    """The same tiny `KEY=VALUE` parser `RailsConfig._load_dotenv_if_present`
    uses (blank/`#` lines skipped, an optional leading `export ` stripped,
    one matching layer of surrounding quotes stripped) -- duplicated rather
    than imported because that one's job is to mutate `os.environ` as a side
    effect, while this one must stay a pure `str -> dict` function so
    `check_env_keys` is trivially unit-testable with a plain string, no
    filesystem or environ involved."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def check_env_file_present(repo_root: Path) -> CheckResult:
    """PASS iff `<repo_root>/.env` exists. Only existence is checked here --
    `check_env_keys` covers the actual key contents, so a `.env` that exists
    but is missing keys reports two distinct, actionable failures instead of
    one vague one."""
    path = repo_root / ".env"
    if path.is_file():
        return CheckResult("env-file", True, str(path))
    return CheckResult(
        "env-file", False, f"{path} not found — copy .env.example and fill in real values"
    )


def check_env_keys(
    *,
    environ: Mapping[str, str] | None = None,
    env_file_text: str | None = None,
) -> CheckResult:
    """PASS iff every `REQUIRED_ENV_KEYS` entry resolves to a non-empty
    value, checking `environ` first and falling back to `env_file_text` --
    the same "a real env var always wins over `.env`" precedence
    `RailsConfig.load()` uses. Pure/data-in: `run_doctor()` reads the real
    `.env` file and `os.environ` and passes them in; tests pass fakes for
    both, no filesystem access required to exercise every branch."""
    environ = environ if environ is not None else os.environ
    from_file = _parse_env_text(env_file_text or "")
    missing = [
        f"{key} (needed by {needed_by})"
        for key, needed_by in REQUIRED_ENV_KEYS.items()
        if not (environ.get(key) or from_file.get(key))
    ]
    if missing:
        return CheckResult("env-keys", False, "missing: " + "; ".join(missing))
    return CheckResult("env-keys", True, f"all {len(REQUIRED_ENV_KEYS)} required keys present")


def check_postgres(
    database_url: str | None,
    *,
    connect: Callable[[str], object] | None = None,
) -> CheckResult:
    """PASS iff a connection to `database_url` opens and `SELECT 1` runs.
    `connect` defaults to `psycopg.connect` (imported lazily so importing
    `rails.doctor` never requires psycopg to be importable) -- injected
    exactly like `rails.github`'s `runner`, so tests exercise every branch
    with a fake connection/cursor, no real Postgres or Docker required."""
    if not database_url:
        return CheckResult("postgres", False, "DATABASE_URL is not set")
    if connect is None:
        import psycopg

        connect = psycopg.connect
    try:
        with connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as exc:
        return CheckResult("postgres", False, f"could not connect: {exc}")
    return CheckResult("postgres", True, "connected, SELECT 1 ok")


def check_migrations(
    database_url: str | None,
    *,
    connect: Callable[[str], object] | None = None,
) -> CheckResult:
    """PASS iff the `vehicles` table exists in `database_url` (a proxy for
    "migrations have been applied" -- see AGENTS.md's module pattern: every
    module ships a migration, and `vehicles` is the reference one, present
    since the very first migration). Same `connect` injection as
    `check_postgres`, and deliberately independent of it: a doctor run
    against a reachable-but-unseeded database should report exactly that,
    not lump "unreachable" and "unmigrated" into one FAIL."""
    if not database_url:
        return CheckResult("migrations", False, "DATABASE_URL is not set")
    if connect is None:
        import psycopg

        connect = psycopg.connect
    try:
        with connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.vehicles')")
                row = cur.fetchone()
    except Exception as exc:
        return CheckResult("migrations", False, f"could not query schema: {exc}")
    exists = bool(row and row[0])
    if exists:
        return CheckResult("migrations", True, "vehicles table present")
    return CheckResult("migrations", False, "vehicles table missing — run `just seed`")


def check_engines(*, which: Callable[[str], str | None] = shutil.which) -> tuple[CheckResult, ...]:
    """One `CheckResult` per supported engine CLI, via `shutil.which` -- the
    same PATH-lookup logic the standalone `rails engines` command uses,
    wrapped as PASS/FAIL. `claude`/`codex` are CRITICAL (see module
    docstring); `gemini` is informational-only."""
    results = []
    for engine in ENGINES:
        path = which(engine)
        critical = engine in _CRITICAL_ENGINES
        if path:
            results.append(CheckResult(f"engine:{engine}", True, path, critical=critical))
        else:
            results.append(
                CheckResult(f"engine:{engine}", False, "not found on PATH", critical=critical)
            )
    return tuple(results)


def check_gh_auth(
    *, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> CheckResult:
    """PASS iff `gh auth status` exits 0. `runner` defaults to
    `subprocess.run` (injected like `rails.github`'s `open_pr`) so tests
    supply a fake result/raise `OSError`-alike without `gh` needing to be
    installed at all."""
    try:
        result = runner(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return CheckResult("gh-auth", False, f"gh not runnable: {exc}")
    text = (result.stderr or result.stdout or "").strip()
    last_line = text.splitlines()[-1] if text else ""
    if result.returncode != 0:
        return CheckResult("gh-auth", False, last_line or "gh auth status failed")
    return CheckResult("gh-auth", True, last_line or "authenticated")


def run_doctor(
    cfg: "RailsConfig",
    *,
    connect: Callable[[str], object] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    gh_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Compose every check above into one report. This is the ONLY function
    that touches the real filesystem (`<repo_root>/.env`) and `os.environ`
    by default -- every check it calls is itself pure/injectable, so tests
    exercise `run_doctor` end-to-end with fakes for `connect`/`which`/
    `gh_runner` and a crafted `environ`/`.env`, never real Docker/gh/network.
    """
    resolved_environ = environ if environ is not None else os.environ
    env_path = cfg.repo_root / ".env"
    try:
        env_text: str | None = env_path.read_text(encoding="utf-8")
    except OSError:
        env_text = None

    database_url = resolved_environ.get("DATABASE_URL")

    checks: list[CheckResult] = [
        check_env_file_present(cfg.repo_root),
        check_env_keys(environ=resolved_environ, env_file_text=env_text),
        check_postgres(database_url, connect=connect),
        check_migrations(database_url, connect=connect),
        *check_engines(which=which),
        check_gh_auth(runner=gh_runner),
    ]
    return DoctorReport(checks=tuple(checks))
