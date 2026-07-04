"""Gemini CLI adapter: builds the `gemini -p ... -o stream-json` argv and
best-effort-parses whatever the stream-json output happens to contain.

Gemini is the bonus/best-effort engine (spec: not gated on parser
perfection). The parser (`parse_gemini_transcript`) was built against a REAL
capture first (tests/rails/fixtures/gemini-transcript.txt for the happy
path, gemini-transcript-error.txt for a real failure) -- see the pinning
tests in tests/rails/test_adapters.py.

Real-capture finding: gemini 0.29.5's `-o stream-json` events use a
`{"type": "message", "role": "user"|"assistant", "content": "..."}` shape
(a flat string, not claude's content-block list) and DO include a terminal
`{"type": "result", "status": "success"|"error", ...}` event in both
captures taken here. Despite that, this adapter still sets
`emits_terminal_result = False` and the parser never sets `saw_result`,
per spec: a single-session capture on one CLI version isn't enough to trust
`result` as reliable across gemini's release cadence and its various
error/interrupt paths (extension/MCP failures were already observed as
stderr noise mid-run in the same capture), so rails treats gemini as
best-effort rather than building a Task-6-facing "explicit result" signal
on a single observation. The `-o` OUTPUT FORMAT flag is otherwise identical
in shape to claude's `--output-format stream-json`, just spelled `-o`.

Isolation flag: `gemini --help` was checked for a `--setting-sources`-style
project/local-only config flag (claude's approach to avoid inheriting the
operator's global config). None was found -- no `--ignore-user-config` /
`--no-project` / equivalent exists in gemini 0.29.5's flag surface (`gemini
mcp` / `gemini extensions` / `gemini hooks` subcommands don't add one
either). Determinism for gemini therefore currently relies on the installed
CLI version and whatever `~/.gemini` global config the operator has, same
as it did before this adapter existed -- not forcing a flag that doesn't
exist, per the Task 2 --bare lesson (a flag chosen for isolation instead of
verified behavior can silently break auth).
"""

from __future__ import annotations

import json
from pathlib import Path

from rails.adapters.base import ParsedTranscript, _SubprocessAdapter


def parse_gemini_transcript(lines: list[str]) -> ParsedTranscript:
    """Parse gemini's `-o stream-json` output, tolerantly.

    - final_message: the ACCUMULATED `content` of every `{"type":
      "message", "role": "assistant"}` event, concatenated in stream order,
      if any such event parsed; else the last non-empty raw stdout line
      (covers non-JSON output, partial/garbled lines, or a shape gemini
      changed under us); else "".

      Accumulation, not "last wins": verified against a real multi-sentence
      capture (tests/rails/fixtures/gemini-transcript-multiline.txt, gemini
      0.29.5) that gemini streams a long reply as multiple `delta:true`
      assistant events whose contents are INCREMENTAL fragments, not
      cumulative snapshots -- the second fragment begins mid-sentence
      (" like sales, finance ...") and must be appended to the first, not
      replace it. gemini emits the fragments already including their own
      leading spaces, so they concatenate with no inserted separator. A
      single-fragment reply (e.g. "pong") accumulates to itself, so the
      short-reply path is unchanged.
    - cost_usd: always None -- gemini reports token counts (`stats` on the
      terminal `result` event), never a dollar figure.
    - result_ok: always True -- never downgraded by parsing; exit code
      alone decides `SessionResult.ok` for this best-effort engine.
    - saw_result: always False -- see module docstring for why the
      observed `result` event isn't trusted as an authoritative signal.

    Never raises: unparseable lines are skipped, not fatal (best-effort
    engine, degrade gracefully).
    """
    assistant_text = ""
    last_nonempty_raw = ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        last_nonempty_raw = line
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            content = event.get("content")
            if isinstance(content, str) and content:
                assistant_text += content

    final_message = assistant_text if assistant_text else last_nonempty_raw

    return ParsedTranscript(
        final_message=final_message,
        cost_usd=None,
        result_ok=True,
        saw_result=False,
    )


class GeminiAdapter(_SubprocessAdapter):
    name = "gemini"
    # gemini has no terminal-result event rails trusts (see module
    # docstring) -- explicit_result is pinned True by the base seam so
    # Task 6's "ok and not explicit_result is suspicious" heuristic never
    # false-flags gemini runs.
    emits_terminal_result = False

    def default_binary(self) -> list[str]:
        return ["gemini"]

    def build_argv(self, prompt: str, *, cwd: Path, out_file: Path) -> list[str]:
        # cwd/out_file are part of the base seam (codex needs them); gemini
        # needs neither -- Popen sets the working directory, and gemini's
        # `-o` selects the OUTPUT FORMAT ("stream-json"), NOT a file path.
        # Passing out_file here would silently become an invalid
        # --output-format value.
        del cwd, out_file
        return [
            *self.binary,
            "-p",
            prompt,
            "--approval-mode",
            "auto_edit",
            "-o",
            "stream-json",
        ]

    def _parse(self, lines: list[str], *, cwd: Path, out_file: Path) -> ParsedTranscript:
        del cwd, out_file  # nothing gemini-specific to read from disk
        return parse_gemini_transcript(lines)
