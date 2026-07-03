"""Shared fixtures for tests/rails.

These are pure-unit / subprocess tests -- no Postgres, no JWKS server. The
root tests/conftest.py wires session fixtures (jwks_server, db_client, ...)
for the FastAPI app; none of it is imported or requested here. Its module
level `os.environ.setdefault("DATABASE_URL", ...)` runs regardless (conftest
files load top-to-bottom for the whole session) but is a harmless no-op for
a package that never touches the DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FAKE_ENGINE = Path(__file__).parent / "fake_engine.py"


@pytest.fixture
def fake_binary():
    """Factory fixture: fake_binary(shape="claude", behavior="ok") returns
    (argv, extra_env).

    argv is the absolute-path invocation of the stub CLI (see fake_engine.py
    for why absolute path, never `python -m`). extra_env carries
    FAKE_SHAPE/FAKE_BEHAVIOR -- callers pass it as the adapter's `extra_env`
    kwarg, which is the ONLY channel these knobs travel through. That proves
    the adapter really does plumb `extra_env` into `RailsConfig.allowed_env`
    rather than, say, setting env vars in the test process directly.
    """

    def _make(shape: str = "claude", behavior: str = "ok") -> tuple[list[str], dict[str, str]]:
        argv = [sys.executable, str(_FAKE_ENGINE)]
        extra_env = {"FAKE_SHAPE": shape, "FAKE_BEHAVIOR": behavior}
        return argv, extra_env

    return _make


@pytest.fixture
def tmp_cwd(tmp_path: Path) -> Path:
    """A scratch cwd for adapter.run() calls -- isolated per test."""
    return tmp_path
