"""Claude Code CLI adapter: builds the `claude -p ...` argv and parses the
`--output-format stream-json` event stream for the final assistant message
and reported cost.

The parser (`parse_claude_transcript`) was built against REAL captured
transcripts first (tests/rails/fixtures/claude-transcript.txt for the happy
path, claude-transcript-budget-exceeded.txt for the no-`result`-field
fallback) -- see the pinning tests in tests/rails/test_adapters.py. The
budget-exceeded capture proved the terminal `result` event does not always
carry a `result` text field (e.g. when a budget cap fires mid-stream), so
the fallback to the last assistant text block is a real path, not just
defensive code.
"""

from __future__ import annotations

import json
from pathlib import Path

from rails.adapters.base import ParsedTranscript, _SubprocessAdapter


def parse_claude_transcript(lines: list[str]) -> ParsedTranscript:
    """Parse newline-delimited claude `stream-json` events.

    - final_message: the terminal `result` event's `result` field if
      present, else the text of the last `assistant` text-content block
      seen, else "" (tolerate absence entirely).
    - cost_usd: the `result` event's `total_cost_usd` field if present,
      else None (tolerate absence).
    - result_ok: False only if a `result` event was seen and its `is_error`
      field is exactly True; True otherwise (including when no `result`
      event was seen at all -- then exit code alone decides).
    - saw_result: whether a terminal `result` event appeared at all;
      surfaces as SessionResult.explicit_result.
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
            if "result" in event and event["result"] is not None:
                result_text = event["result"]
            if "total_cost_usd" in event:
                cost_usd = event["total_cost_usd"]
            is_error = event.get("is_error") is True

    return ParsedTranscript(
        final_message=result_text if result_text is not None else last_assistant_text,
        cost_usd=cost_usd,
        result_ok=not (saw_result and is_error),
        saw_result=saw_result,
    )


class ClaudeAdapter(_SubprocessAdapter):
    name = "claude"
    emits_terminal_result = True  # claude always ends the stream with a `result` event

    def default_binary(self) -> list[str]:
        return ["claude"]

    def build_argv(self, prompt: str, *, cwd: Path, out_file: Path) -> list[str]:
        # cwd/out_file are part of the base seam (codex needs them); claude
        # needs neither -- Popen sets the working directory (so the relative
        # --mcp-config path below resolves against the worktree) and the
        # stream-json result arrives on stdout.
        del cwd, out_file
        argv = [
            *self.binary,
            "-p",
            prompt,
            # --setting-sources project,local: skip user-level (~/.claude)
            # hooks/plugins/memory -- rails sessions must be deterministic
            # across machines, not inherit the operator's ~/.claude (also
            # kills the ~$0.20/session hook overhead observed during fixture
            # capture: $0.2027 -> $0.0436 for the same pong prompt). NOT
            # --bare, although it was the first candidate: --bare hard-locks
            # auth to ANTHROPIC_API_KEY/apiKeyHelper (verified: exits 1 with
            # "Not logged in" under subscription OAuth, which is the only
            # sanctioned auth for rails -- zero API keys, spec §7), and it
            # also skips CLAUDE.md auto-discovery + repo-local .claude
            # skills, which rails sessions rely on (AGENTS.md/skills, Task 7).
            "--setting-sources",
            "project,local",
            "--verbose",
            "--output-format",
            "stream-json",
            # readonly reviewer sessions run in "plan" mode (claude's
            # read-only permission mode: it can read/analyze but not edit);
            # write sessions use "acceptEdits" so the agent can actually
            # apply changes. See rails/agents/loop.py -- the cross-vendor
            # reviewer must never mutate the worktree it's judging.
            "--permission-mode",
            "plan" if self.readonly else "acceptEdits",
            "--max-budget-usd",
            str(self.cfg.max_budget_usd),
        ]
        # Builder (write) sessions get a read-only docs MCP -- context7 -- so
        # they look up CURRENT library APIs (antd v6, react-router, fastapi)
        # instead of hallucinating stale ones. Deliberately scoped:
        #   - claude only: a claude-specific capability; codex/gemini configure
        #     MCP differently, so this stays a documented claude exception
        #     rather than a fake-uniform feature (see rails/AGENTS.md).
        #   - write sessions only: the read-only reviewer judges a diff, not
        #     library docs, and must stay minimal.
        # --strict-mcp-config keeps the session deterministic (ONLY this server
        # loads, ignoring any ambient ~/.claude or repo .mcp.json); --allowedTools
        # pre-approves the context7 tools so the headless session never blocks on
        # a permission prompt. Edits still flow through --permission-mode
        # acceptEdits (verified: the two coexist).
        if not self.readonly:
            argv += [
                # relative -- claude resolves it against its Popen cwd (the
                # worktree), where rails/context7.mcp.json is committed.
                "--mcp-config",
                "rails/context7.mcp.json",
                "--strict-mcp-config",
                "--allowedTools",
                "mcp__context7",
            ]
        return argv

    def _parse(self, lines: list[str], *, cwd: Path, out_file: Path) -> ParsedTranscript:
        del cwd, out_file  # claude's result arrives in-stream; nothing to read from disk
        return parse_claude_transcript(lines)
