"""Claude Code CLI adapter: builds the `claude -p ...` argv and parses the
`--output-format stream-json` event stream for the final assistant message
and reported cost.

The parser (`parse_claude_transcript`) was built against a REAL captured
transcript first (tests/rails/fixtures/claude-transcript.txt, captured with
`claude -p "Reply with exactly the word: pong" --verbose --output-format
stream-json --max-budget-usd 0.05`) -- see
tests/rails/test_adapters.py::test_parse_claude_transcript_from_real_fixture
for the pinning test and what that capture revealed about the real shape
(notably: the terminal `result` event does not always carry a `result`
text field -- e.g. when a budget cap fires mid-stream -- so the fallback to
the last assistant text block is a real path, not just defensive code).
"""

from __future__ import annotations

import json

from rails.adapters.base import _SubprocessAdapter


def parse_claude_transcript(lines: list[str]) -> tuple[str, float | None, bool]:
    """Parse newline-delimited claude `stream-json` events.

    Returns (final_message, cost_usd, result_ok):
      - final_message: the terminal `result` event's `result` field if
        present, else the text of the last `assistant` text-content block
        seen, else "" (tolerate absence entirely).
      - cost_usd: the `result` event's `total_cost_usd` field if present,
        else None (tolerate absence).
      - result_ok: False only if a `result` event was seen and its
        `is_error` field is exactly True; True otherwise (including when no
        `result` event was seen at all -- then exit code alone decides).
    """
    last_assistant_text = ""
    result_text: str | None = None
    cost_usd: float | None = None
    saw_result = False
    is_error = False

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
        if event_type == "assistant":
            message = event.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if text:
                        last_assistant_text = text
        elif event_type == "result":
            saw_result = True
            if "result" in event:
                result_text = event["result"]
            if "total_cost_usd" in event:
                cost_usd = event["total_cost_usd"]
            is_error = event.get("is_error") is True

    final_message = result_text if result_text is not None else last_assistant_text
    result_ok = not (saw_result and is_error)
    return final_message, cost_usd, result_ok


class ClaudeAdapter(_SubprocessAdapter):
    name = "claude"

    def default_binary(self) -> list[str]:
        return ["claude"]

    def build_argv(self, prompt: str) -> list[str]:
        return [
            *self.binary,
            "-p",
            prompt,
            "--verbose",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "acceptEdits",
            "--max-budget-usd",
            str(self.cfg.max_budget_usd),
        ]

    def _parse(self, lines: list[str]) -> tuple[str, float | None, bool]:
        return parse_claude_transcript(lines)
