"""Shared fixtures for tests/rails.

These are pure-unit / subprocess tests -- no Postgres, no JWKS server. The
root tests/conftest.py wires session fixtures (jwks_server, db_client, ...)
for the FastAPI app; none of it is imported or requested here. Its module
level `os.environ.setdefault("DATABASE_URL", ...)` runs regardless (conftest
files load top-to-bottom for the whole session) but is a harmless no-op for
a package that never touches the DB.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import uuid
from pathlib import Path

import pytest

from rails import mission_control

_FAKE_ENGINE = Path(__file__).parent / "fake_engine.py"


def _fake_mission_control_opener(request):
    """Records nothing (no test needs to assert on these calls -- they exist
    purely so `rails.mission_control`'s real functions never touch a real
    socket) and returns just enough of a response for each caller to be
    happy: `start_run` needs `rows[0]["id"]`; `add_step`/`finish_run` use
    `Prefer: return=minimal` and never look at the body at all."""
    if request.full_url.endswith("/rest/v1/agent_runs") and request.get_method() == "POST":
        body = json.dumps([{"id": str(uuid.uuid4())}]).encode("utf-8")
    else:
        body = b""
    return contextlib.closing(io.BytesIO(body))


@pytest.fixture(autouse=True)
def _mission_control_never_touches_the_real_network(monkeypatch):
    """Guarantee that NO test under `tests/rails/` can ever make a real
    network call through `rails.mission_control`, regardless of whether
    `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` happen to be set in the
    environment.

    Why this matters even though tests never set those vars deliberately:
    the repo's `justfile` sets `dotenv-load := true`, so a REAL `rails` run's
    gate step (`uv run pytest -q`, inheriting the calling process's env) runs
    with the repo's real, hosted `.env` values present. `rails.agents.loop`'s
    `_mc_start_run`/`_mc_step`/`_mc_finish` wrappers call `rails.mission_control`
    with no `opener` override, so -- with those hosted credentials genuinely
    set -- they fell through to the real default (`urllib.request.urlopen`)
    and made real PostgREST writes of THIS test suite's fixture data into the
    HOSTED `agent_runs`/`run_steps` tables (confirmed live: one real run
    injected 43 junk rows into the deployed `/mission-control` dashboard).

    The seam: `start_run`/`add_step`/`finish_run` each accept `opener` as a
    KEYWORD-ONLY parameter defaulting to `urllib.request.urlopen`. That
    default is a plain default-argument value, bound once to the real
    `urlopen` function object when `rails.mission_control` is first imported
    -- reassigning the `urllib.request.urlopen` attribute afterwards would
    never be seen by an already-bound default. So instead we overwrite each
    function's OWN bound default directly, via its `__kwdefaults__` (where
    keyword-only defaults live) -- `monkeypatch.setitem` restores the
    original real default after every test.

    Tests that pass their own explicit `opener=...` (every test in
    `test_mission_control.py`) are completely unaffected: an explicit keyword
    argument always wins over a function's default. Tests that replace
    `rails.mission_control.start_run`/`add_step`/`finish_run` wholesale (the
    `FakeMissionControl`-based tests in `test_loop.py`) are also unaffected --
    they swap the module attribute itself, which this fixture never touches.
    """
    for fn in (mission_control.start_run, mission_control.add_step, mission_control.finish_run):
        monkeypatch.setitem(fn.__kwdefaults__, "opener", _fake_mission_control_opener)


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
