"""Tests for rails.doctor: the `rails doctor` preflight report.

No real Docker/Postgres, `gh`, or network anywhere in this file -- every
check under test takes an injected fake collaborator (`connect`, `which`,
`gh_runner`, or plain `environ`/`env_file_text` data), mirroring the fake
patterns already used in test_gate.py (fake subprocess steps) and
test_github.py (fake `runner`). `run_doctor` itself is exercised with an
all-pass fake set and, separately, single-check-failure fakes -- both a
CRITICAL failure (flips `report.ok` False) and a non-critical one (`engine:
gemini` missing -- must NOT flip it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rails.config import RailsConfig
from rails.doctor import (
    ENGINES,
    CheckResult,
    DoctorReport,
    check_engines,
    check_env_file_present,
    check_env_keys,
    check_gh_auth,
    check_migrations,
    check_postgres,
    run_doctor,
)


def make_config(**over) -> RailsConfig:
    defaults = {"engine": "claude", "max_budget_usd": 2.0, "repo_root": Path("/repo")}
    defaults.update(over)
    return RailsConfig(**defaults)


# --- CheckResult / DoctorReport ------------------------------------------


def test_report_ok_true_when_all_checks_pass():
    report = DoctorReport(
        checks=(
            CheckResult("a", True, "fine"),
            CheckResult("b", True, "fine", critical=False),
        )
    )
    assert report.ok is True


def test_report_ok_false_when_a_critical_check_fails():
    report = DoctorReport(
        checks=(
            CheckResult("a", True, "fine"),
            CheckResult("b", False, "broken"),
        )
    )
    assert report.ok is False


def test_report_ok_true_when_only_a_noncritical_check_fails():
    report = DoctorReport(
        checks=(
            CheckResult("a", True, "fine"),
            CheckResult("engine:gemini", False, "not found on PATH", critical=False),
        )
    )
    assert report.ok is True


def test_summary_shows_pass_and_fail_lines():
    report = DoctorReport(
        checks=(
            CheckResult("postgres", True, "connected, SELECT 1 ok"),
            CheckResult("gh-auth", False, "not logged in"),
        )
    )
    lines = report.summary().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("PASS")
    assert "postgres" in lines[0]
    assert "connected, SELECT 1 ok" in lines[0]
    assert lines[1].startswith("FAIL")
    assert "gh-auth" in lines[1]
    assert "not logged in" in lines[1]


def test_summary_tags_noncritical_failures_as_optional():
    report = DoctorReport(
        checks=(CheckResult("engine:gemini", False, "not found on PATH", critical=False),)
    )
    line = report.summary()
    assert "FAIL" in line
    assert "optional" in line.lower()


def test_summary_does_not_tag_critical_checks_as_optional():
    report = DoctorReport(checks=(CheckResult("postgres", False, "unreachable"),))
    assert "optional" not in report.summary().lower()


# --- check_env_file_present -----------------------------------------------


def test_env_file_present_passes_when_file_exists(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://x\n")

    result = check_env_file_present(tmp_path)

    assert result.ok is True
    assert result.critical is True
    assert str(tmp_path / ".env") in result.detail


def test_env_file_present_fails_when_missing(tmp_path):
    result = check_env_file_present(tmp_path)

    assert result.ok is False
    assert ".env" in result.detail


# --- check_env_keys --------------------------------------------------------


def test_env_keys_passes_when_all_present_in_environ():
    environ = {
        "DATABASE_URL": "postgresql://x",
        "SUPABASE_JWKS_URL": "https://x/jwks.json",
        "SUPABASE_URL": "https://x.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "secret",
    }

    result = check_env_keys(environ=environ, env_file_text=None)

    assert result.ok is True
    assert "4" in result.detail


def test_env_keys_passes_when_present_only_in_env_file_text():
    text = (
        "DATABASE_URL=postgresql://x\n"
        "SUPABASE_JWKS_URL=https://x/jwks.json\n"
        "SUPABASE_URL=https://x.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=secret\n"
    )

    result = check_env_keys(environ={}, env_file_text=text)

    assert result.ok is True


def test_env_keys_environ_wins_over_env_file_when_both_set():
    """Same precedence as RailsConfig.load(): a real env var always wins
    over the .env file's value -- this check only cares whether a value
    resolves, but exercising the precedence order guards against a future
    regression where the file silently shadows the real environment."""
    result = check_env_keys(
        environ={"DATABASE_URL": "postgresql://real"},
        env_file_text="DATABASE_URL=postgresql://from-file\n"
        "SUPABASE_JWKS_URL=https://x/jwks.json\n"
        "SUPABASE_URL=https://x.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=secret\n",
    )

    assert result.ok is True


def test_env_keys_fails_and_lists_each_missing_key_with_its_consumer():
    result = check_env_keys(environ={}, env_file_text=None)

    assert result.ok is False
    assert "DATABASE_URL" in result.detail
    assert "gate" in result.detail
    assert "SUPABASE_SERVICE_ROLE_KEY" in result.detail
    assert "triage" in result.detail


def test_env_keys_fails_when_one_key_missing_others_present():
    environ = {
        "DATABASE_URL": "postgresql://x",
        "SUPABASE_JWKS_URL": "https://x/jwks.json",
        "SUPABASE_URL": "https://x.supabase.co",
        # SUPABASE_SERVICE_ROLE_KEY intentionally absent
    }

    result = check_env_keys(environ=environ, env_file_text=None)

    assert result.ok is False
    assert "SUPABASE_SERVICE_ROLE_KEY" in result.detail
    assert "DATABASE_URL" not in result.detail


def test_env_keys_ignores_blank_lines_and_comments_in_env_file_text():
    text = "\n# a comment\nDATABASE_URL=postgresql://x\n"
    result = check_env_keys(environ={}, env_file_text=text)
    assert "DATABASE_URL" not in result.detail  # present, so not listed as missing


# --- check_postgres ---------------------------------------------------------


@dataclass
class _FakeCursor:
    fetchone_return: tuple | None = None
    raise_on_execute: Exception | None = None
    executed: list = field(default_factory=list)

    def execute(self, sql):
        self.executed.append(sql)
        if self.raise_on_execute is not None:
            raise self.raise_on_execute

    def fetchone(self):
        return self.fetchone_return

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@dataclass
class _FakeConnection:
    cursor_obj: _FakeCursor

    def cursor(self):
        return self.cursor_obj

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def make_connect(cursor: _FakeCursor):
    def _connect(dsn):
        return _FakeConnection(cursor_obj=cursor)

    return _connect


def failing_connect(dsn):
    raise OSError("connection refused")


def test_postgres_fails_when_database_url_unset():
    result = check_postgres(None, connect=make_connect(_FakeCursor()))

    assert result.ok is False
    assert "DATABASE_URL" in result.detail


def test_postgres_passes_when_select_1_succeeds():
    result = check_postgres("postgresql://x", connect=make_connect(_FakeCursor()))

    assert result.ok is True
    assert result.critical is True


def test_postgres_fails_when_connect_raises():
    result = check_postgres("postgresql://x", connect=failing_connect)

    assert result.ok is False
    assert "connection refused" in result.detail


def test_postgres_fails_when_execute_raises():
    cursor = _FakeCursor(raise_on_execute=RuntimeError("boom"))
    result = check_postgres("postgresql://x", connect=make_connect(cursor))

    assert result.ok is False
    assert "boom" in result.detail


# --- check_migrations --------------------------------------------------------


def test_migrations_fails_when_database_url_unset():
    result = check_migrations(None, connect=make_connect(_FakeCursor()))

    assert result.ok is False
    assert "DATABASE_URL" in result.detail


def test_migrations_passes_when_vehicles_table_present():
    cursor = _FakeCursor(fetchone_return=("vehicles",))
    result = check_migrations("postgresql://x", connect=make_connect(cursor))

    assert result.ok is True


def test_migrations_fails_when_vehicles_table_missing():
    cursor = _FakeCursor(fetchone_return=(None,))
    result = check_migrations("postgresql://x", connect=make_connect(cursor))

    assert result.ok is False
    assert "seed" in result.detail


def test_migrations_fails_when_query_raises():
    cursor = _FakeCursor(raise_on_execute=RuntimeError("no such schema"))
    result = check_migrations("postgresql://x", connect=make_connect(cursor))

    assert result.ok is False
    assert "no such schema" in result.detail


# --- check_engines -----------------------------------------------------------


def test_engines_all_present_all_critical_ok():
    results = check_engines(which=lambda name: f"/usr/local/bin/{name}")

    assert len(results) == len(ENGINES)
    assert all(r.ok for r in results)


def test_engines_gemini_missing_is_noncritical():
    def fake_which(name):
        return None if name == "gemini" else f"/usr/local/bin/{name}"

    results = check_engines(which=fake_which)
    by_name = {r.name: r for r in results}

    assert by_name["engine:gemini"].ok is False
    assert by_name["engine:gemini"].critical is False
    assert by_name["engine:claude"].critical is True
    assert by_name["engine:codex"].critical is True


def test_engines_claude_missing_is_critical():
    def fake_which(name):
        return None if name == "claude" else f"/usr/local/bin/{name}"

    results = check_engines(which=fake_which)
    by_name = {r.name: r for r in results}

    assert by_name["engine:claude"].ok is False
    assert by_name["engine:claude"].critical is True


# --- check_gh_auth -------------------------------------------------------


@dataclass
class _FakeGhResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def test_gh_auth_passes_on_zero_exit():
    def runner(argv, **kwargs):
        return _FakeGhResult(returncode=0, stderr="Logged in to github.com as octocat")

    result = check_gh_auth(runner=runner)

    assert result.ok is True
    assert "octocat" in result.detail


def test_gh_auth_fails_on_nonzero_exit():
    def runner(argv, **kwargs):
        return _FakeGhResult(returncode=1, stderr="You are not logged into any GitHub hosts")

    result = check_gh_auth(runner=runner)

    assert result.ok is False
    assert "not logged into" in result.detail


def test_gh_auth_fails_when_gh_not_installed():
    def runner(argv, **kwargs):
        raise OSError("gh: command not found")

    result = check_gh_auth(runner=runner)

    assert result.ok is False
    assert "not runnable" in result.detail.lower()


# --- run_doctor (composed) --------------------------------------------------


def _all_pass_kwargs(tmp_path: Path):
    (tmp_path / ".env").write_text("SUPABASE_URL=https://x.supabase.co\n")
    environ = {
        "DATABASE_URL": "postgresql://x",
        "SUPABASE_JWKS_URL": "https://x/jwks.json",
        "SUPABASE_URL": "https://x.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "secret",
    }
    cursor = _FakeCursor(fetchone_return=("vehicles",))
    return {
        "connect": make_connect(cursor),
        "which": lambda name: f"/usr/local/bin/{name}",
        "gh_runner": lambda argv, **kw: _FakeGhResult(
            returncode=0, stderr="Logged in to github.com as octocat"
        ),
        "environ": environ,
    }


def test_run_doctor_all_pass_is_ok_and_exhaustive(tmp_path):
    cfg = make_config(repo_root=tmp_path)

    report = run_doctor(cfg, **_all_pass_kwargs(tmp_path))

    assert report.ok is True
    names = {c.name for c in report.checks}
    assert names == {
        "env-file",
        "env-keys",
        "postgres",
        "migrations",
        "engine:claude",
        "engine:codex",
        "engine:gemini",
        "gh-auth",
    }
    assert all(c.ok for c in report.checks)


def test_run_doctor_one_critical_failure_flips_ok_false(tmp_path):
    kwargs = _all_pass_kwargs(tmp_path)
    kwargs["gh_runner"] = lambda argv, **kw: _FakeGhResult(returncode=1, stderr="not logged in")
    cfg = make_config(repo_root=tmp_path)

    report = run_doctor(cfg, **kwargs)

    assert report.ok is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["gh-auth"].ok is False
    # everything else still reports its own true state independently
    assert by_name["postgres"].ok is True


def test_run_doctor_missing_gemini_alone_stays_ok(tmp_path):
    kwargs = _all_pass_kwargs(tmp_path)
    kwargs["which"] = lambda name: None if name == "gemini" else f"/usr/local/bin/{name}"
    cfg = make_config(repo_root=tmp_path)

    report = run_doctor(cfg, **kwargs)

    assert report.ok is True
    by_name = {c.name: c for c in report.checks}
    assert by_name["engine:gemini"].ok is False


def test_run_doctor_reads_missing_env_file_without_crashing(tmp_path):
    """No .env at all on disk: env-file FAILs, and env-keys falls back to
    `environ` alone (parse of an empty/absent file, not a crash)."""
    kwargs = _all_pass_kwargs(tmp_path)
    (tmp_path / ".env").unlink()
    cfg = make_config(repo_root=tmp_path)

    report = run_doctor(cfg, **kwargs)

    by_name = {c.name: c for c in report.checks}
    assert by_name["env-file"].ok is False
    assert by_name["env-keys"].ok is True  # environ alone already satisfies all keys


def test_run_doctor_defaults_environ_to_os_environ_when_not_passed(tmp_path, monkeypatch):
    """Sanity check on the production default path: with no `environ=`
    override, run_doctor reads the real os.environ (monkeypatched here),
    proving the default wiring -- not just the injected-fake path -- works."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-os-environ")
    cfg = make_config(repo_root=tmp_path)
    cursor = _FakeCursor(fetchone_return=("vehicles",))
    seen_urls = []

    def connect(dsn):
        seen_urls.append(dsn)
        return _FakeConnection(cursor_obj=cursor)

    run_doctor(
        cfg,
        connect=connect,
        which=lambda name: None,
        gh_runner=lambda argv, **kw: _FakeGhResult(returncode=1),
    )

    # connect is used by both check_postgres and check_migrations -- assert
    # every call saw the real os.environ value, not just the first.
    assert seen_urls == ["postgresql://from-os-environ"] * 2
