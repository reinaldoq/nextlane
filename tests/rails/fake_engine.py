#!/usr/bin/env python3
"""Stub engine CLI used by adapter tests.

Invoked BY ABSOLUTE PATH — `[sys.executable, str(Path(__file__).parent /
"fake_engine.py")]` — NEVER `python -m tests.rails.fake_engine`: real
adapters run with `cwd` set to a worktree/tmp dir and an env built from
`RailsConfig.allowed_env()` (no `PYTHONPATH`), so `-m` module resolution
would break exactly the way it would for a real deployment. This file must
therefore be a fully standalone script with no package-relative imports.

Behavior is controlled entirely by env vars so adapters under test never
need special-case argv handling — arbitrary argv is accepted and ignored,
letting real CLI argv shapes (e.g. the claude adapter's `-p <prompt>
--verbose ...`) pass straight through without this stub parsing them.

FAKE_BEHAVIOR:
  ok              -- emit a normal transcript for FAKE_SHAPE, exit 0
  ok_no_result    -- like ok but WITHOUT the terminal result event (exit 0):
                     exercises SessionResult.explicit_result=False
  fail            -- emit a normal transcript for FAKE_SHAPE, exit 1
  timeout         -- write our own pid to "fake_engine.pid" (so tests can
                     confirm we actually die), then sleep 300s
  echo_env        -- print os.environ as a single JSON line, exit 0 (used to
                     prove the env whitelist boundary holds through the
                     adapter, not just in RailsConfig unit tests)
  write_file:<relpath>:<content>
                  -- write <content> to <relpath> under cwd, then behave
                     like "ok" (relpath/content may not contain ":" beyond
                     the two separators — content is everything after the
                     second ":")

FAKE_SHAPE = claude | codex | gemini -- which engine's real output shape to
  emit. Field names are lifted from one manually captured real run per
  engine (see tests/rails/fixtures/); claude's shape is exercised by real
  adapter parsing today, codex/gemini are placeholders for Task 3.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FINAL_TEXT = "fake engine final message"
FAKE_COST_USD = 0.0123


def _emit_claude(ok: bool, with_result: bool = True) -> None:
    # Shape mirrors tests/rails/fixtures/claude-transcript.txt (real capture,
    # claude 2.1.198 with --setting-sources project,local): init +
    # rate_limit_event + assistant + terminal result event.
    events = [
        {
            "type": "system",
            "subtype": "init",
            "cwd": os.getcwd(),
            "session_id": "fake-session",
            "model": "fake-model",
            "permissionMode": "default",
        },
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed"},
            "session_id": "fake-session",
        },
        {
            "type": "assistant",
            "message": {
                "model": "fake-model",
                "role": "assistant",
                "content": [{"type": "text", "text": FINAL_TEXT}],
            },
        },
    ]
    if with_result:
        events.append(
            {
                "type": "result",
                "subtype": "success" if ok else "error",
                "is_error": not ok,
                "result": FINAL_TEXT,
                "total_cost_usd": FAKE_COST_USD,
            }
        )
    for event in events:
        print(json.dumps(event), flush=True)


def _emit_codex(ok: bool) -> None:
    # Placeholder shape for Task 3 -- codex adapter/parser not built yet.
    events = [
        {"type": "task_started"},
        {"type": "agent_message", "message": FINAL_TEXT},
        {"type": "task_complete", "is_error": not ok},
    ]
    for event in events:
        print(json.dumps(event), flush=True)


def _emit_gemini(ok: bool) -> None:
    # Placeholder shape for Task 3 -- gemini adapter/parser not built yet.
    events = [
        {"type": "content", "text": "thinking..."},
        {"type": "content", "text": FINAL_TEXT},
    ]
    for event in events:
        print(json.dumps(event), flush=True)
    if not ok:
        print("error: fake gemini failure", file=sys.stderr, flush=True)


_EMITTERS = {"claude": _emit_claude, "codex": _emit_codex, "gemini": _emit_gemini}


def main() -> int:
    behavior = os.environ.get("FAKE_BEHAVIOR", "ok")
    shape = os.environ.get("FAKE_SHAPE", "claude")

    if behavior == "timeout":
        Path("fake_engine.pid").write_text(str(os.getpid()))
        time.sleep(300)
        return 0

    if behavior == "echo_env":
        print(json.dumps(dict(os.environ)), flush=True)
        return 0

    if behavior.startswith("write_file:"):
        _, relpath, content = behavior.split(":", 2)
        Path(relpath).write_text(content)
        behavior = "ok"

    if behavior == "ok_no_result":
        _emit_claude(True, with_result=False)
        return 0

    ok = behavior == "ok"
    emitter = _EMITTERS.get(shape, _emit_claude)
    emitter(ok)
    if not ok:
        print("fake engine reporting failure", file=sys.stderr, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
