"""Codex CLI adapter: builds the `codex exec ...` argv and parses the
`--json` stream-of-events output for the terminal turn state, plus the `-o`
(`--output-last-message`) sidecar file codex writes on a successful turn.

The parser (`parse_codex_transcript`) was built against REAL captures first
(tests/rails/fixtures/codex-transcript.txt for the happy path,
codex-transcript-error.txt for a real failure) -- see the pinning tests in
tests/rails/test_adapters.py. codex-cli 0.141.0's `--json` stream is a
different event vocabulary from claude's: `thread.started` / `turn.started`
/ `item.completed` (an `agent_message` item carries the assistant text) /
the terminal event, which is `turn.completed` (with a `usage` token-count
block, no dollar cost) on success or `turn.failed` (with an `error` block)
on failure. A real capture with a bogus `-m` model value also showed a
mid-stream top-level `{"type": "error", ...}` event and an
`item.completed` item of type "error" -- both are in-band diagnostics, not
terminal events, and are ignored by the parser.

Real-capture finding on the `-o` file: it is codex's most reliable source
for the final message (no truncation risk from us missing an event type
across CLI versions), but codex ONLY writes it on a successful turn -- a
real run with a bogus model and `-o` set produced no output file at all.
The parser therefore prefers the `-o` file when present and non-empty, and
falls back to the last in-stream `agent_message` text otherwise (e.g. on
failure, or if a future codex version stops writing the file for some other
reason).
"""

from __future__ import annotations

import json
from pathlib import Path

from rails.adapters.base import ParsedTranscript, _SubprocessAdapter


def parse_codex_transcript(lines: list[str], *, cwd: Path, out_file: Path) -> ParsedTranscript:
    """Parse newline-delimited codex `--json` events plus the `-o` sidecar.

    - final_message: the `-o` file's contents (stripped) if the file exists
      and is non-empty, else the text of the last `item.completed`
      `agent_message` item seen in-stream, else "".
    - cost_usd: always None -- codex reports token counts (`usage` on
      `turn.completed`), never a dollar figure. Tolerated, not computed.
    - result_ok: False only if a `turn.failed` event was seen (and no later
      `turn.completed` overrides it); True otherwise, including when no
      terminal event was seen at all -- then exit code alone decides.
    - saw_result: whether a `turn.completed` or `turn.failed` terminal event
      appeared at all; surfaces as SessionResult.explicit_result.
    """
    del cwd  # part of the base seam; codex needs only out_file, not cwd, to parse
    last_agent_text = ""
    saw_result = False
    result_ok = True

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if text:
                    last_agent_text = text
        elif event_type == "turn.completed":
            saw_result = True
            result_ok = True
        elif event_type == "turn.failed":
            saw_result = True
            result_ok = False

    final_message = last_agent_text
    if out_file.exists():
        try:
            file_text = out_file.read_text(encoding="utf-8").strip()
        except OSError:
            file_text = ""
        if file_text:
            final_message = file_text

    return ParsedTranscript(
        final_message=final_message,
        cost_usd=None,
        result_ok=result_ok,
        saw_result=saw_result,
    )


class CodexAdapter(_SubprocessAdapter):
    name = "codex"
    emits_terminal_result = True  # codex ends the turn with turn.completed/turn.failed

    def default_binary(self) -> list[str]:
        return ["codex"]

    def build_argv(self, prompt: str, *, cwd: Path, out_file: Path) -> list[str]:
        return [
            *self.binary,
            "exec",
            "-s",
            "workspace-write",
            "--json",
            # --ignore-user-config: skip $CODEX_HOME/config.toml -- rails
            # sessions must be deterministic across machines, not inherit
            # the operator's global codex config (same rationale as
            # claude's --setting-sources project,local). Verified against a
            # real subscription-auth run: auth still works (codex's own
            # --help notes "auth still uses CODEX_HOME"), only the
            # optional user-level config layer is skipped.
            "--ignore-user-config",
            # -C: codex's working-root flag (Popen's cwd= isn't enough --
            # codex needs to be told explicitly which directory is its
            # workspace root for the sandbox policy).
            "-C",
            str(cwd),
            # -o/--output-last-message: the reliable final-message sink --
            # see module docstring for why the in-stream agent_message
            # alone isn't trusted as primary.
            "-o",
            str(out_file),
            # CRITICAL: prompt is the trailing POSITIONAL arg. codex's -p
            # means --profile (layers a named config profile), NOT
            # "prompt" -- using -p here would silently misroute the prompt
            # text into a profile-name lookup instead of driving the
            # session.
            prompt,
        ]

    def _parse(self, lines: list[str], *, cwd: Path, out_file: Path) -> ParsedTranscript:
        return parse_codex_transcript(lines, cwd=cwd, out_file=out_file)
