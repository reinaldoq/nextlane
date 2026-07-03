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

# Base whitelist for agent-session child processes. GIT_* is handled
# separately below since it's a prefix match, not a fixed key.
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


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
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
        current repo's git metadata."""
        return cls(
            engine=os.environ.get("RAILS_ENGINE", "claude"),
            max_budget_usd=float(os.environ.get("RAILS_MAX_BUDGET_USD", "2.0")),
            repo_root=_repo_root(),
        )

    def allowed_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Build a child-process env for agent-session subprocesses from a
        whitelist -- PATH/HOME/SHELL/TERM/LANG/LC_ALL/USER/TMPDIR plus any
        GIT_* vars present -- plus explicitly-passed extras (which win on
        conflict). Anything else in `os.environ` (secrets, unrelated
        canaries, ...) is never forwarded.
        """
        env: dict[str, str] = {}
        for key in _BASE_ENV_WHITELIST:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        for key, value in os.environ.items():
            if key.startswith("GIT_"):
                env[key] = value
        if extra:
            env.update(extra)
        return env
