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
    max_budget_usd: float
    repo_root: Path

    @classmethod
    def load(cls) -> RailsConfig:
        """Read configuration from the environment (with defaults) and the
        current repo's git metadata. Not memoized: every call re-reads the
        environment and re-runs git rev-parse."""
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
            repo_root=_repo_root(),
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
