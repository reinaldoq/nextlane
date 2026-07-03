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
--verbose ...`) pass straight through without this stub parsing them. The
ONE exception: FAKE_SHAPE=codex on a successful run looks for `-o <path>`
in argv and writes FINAL_TEXT there, because real codex's reliable final
message lives in that sidecar file, not on stdout (see rails/adapters/codex.py)
-- a stub that never wrote it could never exercise that seam.

FAKE_BEHAVIOR:
  ok              -- emit a normal transcript for FAKE_SHAPE, exit 0
  ok_no_result    -- like ok but WITHOUT the terminal result event (exit 0):
                     exercises SessionResult.explicit_result=False
  bad_utf8        -- like ok but with a raw non-UTF-8 line (0xff bytes)
                     emitted BETWEEN a valid event and the terminal result
                     event: exercises the adapter's errors="replace" decode
                     (a strict decoder dies mid-stream and loses the result)
  fail            -- emit a normal transcript for FAKE_SHAPE, exit 1
  timeout         -- write our own pid to "fake_engine.pid" (so tests can
                     confirm we actually die), then sleep 300s
  echo_env        -- print os.environ as a single JSON line, exit 0 (used to
                     prove the env whitelist boundary holds through the
                     adapter, not just in RailsConfig unit tests)
  write_file:<relpath>:<content>[:<outcome>]
                  -- write <content> to <relpath> under cwd, then behave
                     like <outcome> ("ok" or "fail", default "ok"). The
                     fail composition simulates an engine that edited files
                     THEN exited nonzero (Task 6's same-worktree retry).
                     Neither relpath nor content may contain ":".

FAKE_SHAPE = claude | codex | gemini -- which engine's real output shape to
  emit. Field names are lifted from one manually captured real run per
  engine (see tests/rails/fixtures/): claude-transcript*.txt,
  codex-transcript*.txt (codex-cli 0.141.0), gemini-transcript*.txt (gemini
  0.29.5).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FINAL_TEXT = "fake engine final message"
FAKE_COST_USD = 0.0123


def _claude_events(ok: bool) -> list[dict]:
    # Shape mirrors tests/rails/fixtures/claude-transcript.txt (real capture,
    # claude 2.1.198 with --setting-sources project,local): init +
    # rate_limit_event + assistant + terminal result event.
    return [
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
        {
            "type": "result",
            "subtype": "success" if ok else "error",
            "is_error": not ok,
            "result": FINAL_TEXT,
            "total_cost_usd": FAKE_COST_USD,
        },
    ]


def _emit_claude(ok: bool, with_result: bool = True) -> None:
    events = _claude_events(ok)
    if not with_result:
        events = events[:-1]
    for event in events:
        print(json.dumps(event), flush=True)


def _emit_claude_bad_utf8() -> None:
    """Valid events, then a raw non-UTF-8 line, then the terminal result
    event -- so a strict-decoding adapter loses the result event while a
    replace-decoding one keeps everything."""
    events = _claude_events(True)
    for event in events[:-1]:
        print(json.dumps(event), flush=True)
    sys.stdout.flush()
    sys.stdout.buffer.write(b"\xff raw binary noise \xff\n")
    sys.stdout.buffer.flush()
    print(json.dumps(events[-1]), flush=True)


def _write_codex_out_file() -> None:
    """Mirror real codex: on a successful turn it writes the `-o <path>`
    sidecar file with the final message (no trailing newline). Real codex
    was verified to NOT write this file at all on a failed turn, so this is
    only ever called from the ok path."""
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "-o" and i + 1 < len(argv):
            Path(argv[i + 1]).write_text(FINAL_TEXT)
            return


def _emit_codex(ok: bool) -> None:
    # Shape mirrors tests/rails/fixtures/codex-transcript.txt /
    # codex-transcript-error.txt (real capture, codex-cli 0.141.0):
    # thread.started + turn.started + item.completed (agent_message), then
    # the terminal event -- turn.completed (usage, no dollar cost) on
    # success, turn.failed (error) on failure.
    events = [
        {"type": "thread.started", "thread_id": "fake-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": FINAL_TEXT},
        },
    ]
    if ok:
        events.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 0,
                },
            }
        )
    else:
        events.append({"type": "turn.failed", "error": {"message": "fake codex failure"}})
    for event in events:
        print(json.dumps(event), flush=True)
    if ok:
        _write_codex_out_file()


def _emit_gemini(ok: bool) -> None:
    # Shape mirrors tests/rails/fixtures/gemini-transcript.txt /
    # gemini-transcript-error.txt (real capture, gemini 0.29.5): init +
    # message(role=user) + message(role=assistant) [success only] + a
    # terminal `result` event (present in both captures, but never trusted
    # as authoritative -- see rails/adapters/gemini.py module docstring).
    events = [
        {"type": "init", "session_id": "fake-session", "model": "fake-model"},
        {"type": "message", "role": "user", "content": "prompt"},
    ]
    if ok:
        events.append(
            {"type": "message", "role": "assistant", "content": FINAL_TEXT, "delta": True}
        )
        events.append({"type": "result", "status": "success", "stats": {"total_tokens": 10}})
    else:
        events.append(
            {"type": "result", "status": "error", "error": {"message": "fake gemini failure"}}
        )
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
        parts = behavior.split(":")
        if len(parts) == 4:
            _, relpath, content, outcome = parts
        else:
            _, relpath, content = parts
            outcome = "ok"
        Path(relpath).write_text(content)
        behavior = outcome

    if behavior == "ok_no_result":
        _emit_claude(True, with_result=False)
        return 0

    if behavior == "bad_utf8":
        _emit_claude_bad_utf8()
        return 0

    ok = behavior == "ok"
    emitter = _EMITTERS.get(shape, _emit_claude)
    emitter(ok)
    if not ok:
        print("fake engine reporting failure", file=sys.stderr, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
