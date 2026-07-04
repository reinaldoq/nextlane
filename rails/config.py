"""Runner configuration: engine selection, budgets, and the subprocess env
whitelist used when driving agent CLI sessions.

Spec §7 (Rails / agent security): sessions run in isolated git worktrees with
restricted permission modes and only whitelisted env vars passed through --
never `os.environ` wholesale. `allowed_env()` is that whitelist boundary.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RailsConfigError(Exception):
    """Configuration could not be loaded (bad env value, not a git repo, ...)."""


# Base whitelist for agent-session child processes.
_BASE_ENV_WHITELIST = (
    "PATH",
    "HOME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_ALL",
    "USER",
    "TMPDIR",
)

# explicit allowlist — GIT_SSH_COMMAND/GIT_ASKPASS/GIT_DIR etc. are
# command-execution or isolation-escape hooks; never forward the GIT_
# namespace wholesale (spec §7).
_GIT_ENV_WHITELIST = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


def _load_dotenv_if_present(repo_root: Path) -> None:
    """Load `<repo_root>/.env` into `os.environ` for local `rails` CLI runs
    -- audit bug 2: `uv run rails ...` (unlike the `just` recipes, which get
    `set dotenv-load := true` for free) never loaded `.env` on its own, so a
    bare `uv run rails triage` failed with a missing SUPABASE_URL/
    SERVICE_ROLE_KEY outside of a `just` recipe.

    A KEY already present in `os.environ` is NEVER overridden -- the real
    environment (CI, an explicit shell export, `FOO=bar uv run rails ...`)
    always wins over the `.env` file's value for that same key. CI sets real
    env vars directly and has no `.env` file to begin with, so this is a
    local-dev-only convenience with no effect on CI.

    Tiny stdlib `KEY=VALUE` parser -- deliberately NOT a python-dotenv
    dependency. Blank lines and `#`-comments are skipped; a leading `export
    ` is stripped (so a `.env` copy-pasted from a shell profile still works);
    a single matching layer of surrounding single/double quotes is stripped
    from the value. A line with no `=` (or a missing/empty file) is silently
    skipped/tolerated rather than raising -- a hand-edited `.env`'s stray
    line must never crash the whole CLI.
    """
    env_path = repo_root / ".env"
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
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
        if key and key not in os.environ:
            os.environ[key] = value


def _repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RailsConfigError(
            "not inside a git repository — run rails from the repo root"
        ) from exc
    return Path(result.stdout.strip())


@dataclass(frozen=True)
class RailsConfig:
    """Immutable runner configuration. Build via `RailsConfig.load()`."""

    engine: str
    # max_budget_usd is a HARD cap only for claude (its --max-budget-usd
    # flag). codex and gemini expose no USD budget flag (they report token
    # counts, not dollars -- see rails/adapters/codex.py & gemini.py), so
    # for those engines the session blast radius is bounded by timeout +
    # bounded retries + the sandbox/approval mode ALONE, not by spend. Task
    # 7's AGENTS.md must state this asymmetry explicitly.
    max_budget_usd: float
    repo_root: Path

    @classmethod
    def load(cls) -> RailsConfig:
        """Read configuration from the environment (with defaults) and the
        current repo's git metadata. Not memoized: every call re-reads the
        environment and re-runs git rev-parse.

        Auto-loads `<repo_root>/.env` first (see `_load_dotenv_if_present`)
        so a bare `uv run rails ...` behaves like the `just` recipes
        (`set dotenv-load := true`) without requiring `just` -- a real,
        already-set env var always wins over the `.env` file's value."""
        repo_root = _repo_root()
        _load_dotenv_if_present(repo_root)
        raw_budget = os.environ.get("RAILS_MAX_BUDGET_USD", "2.0")
        try:
            max_budget_usd = float(raw_budget)
        except ValueError as exc:
            raise RailsConfigError(
                f"RAILS_MAX_BUDGET_USD must be a number, got '{raw_budget}'"
            ) from exc
        return cls(
            engine=os.environ.get("RAILS_ENGINE", "claude"),
            max_budget_usd=max_budget_usd,
            repo_root=repo_root,
        )

    def allowed_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build a child-process env for agent-session subprocesses from a
        whitelist -- PATH/HOME/SHELL/TERM/LANG/LC_ALL/USER/TMPDIR plus the
        four GIT_ identity vars -- plus explicitly-passed extras (which win
        on conflict). Anything else in `os.environ` (secrets, git execution
        hooks, unrelated canaries, ...) is never forwarded.
        """
        env: dict[str, str] = {}
        for key in _BASE_ENV_WHITELIST + _GIT_ENV_WHITELIST:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        if extra:
            env.update(extra)
        return env
