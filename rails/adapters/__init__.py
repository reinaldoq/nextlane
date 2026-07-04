"""Adapter registry: `get_adapter(engine, cfg, binary=None)` returns the
right `AgentSession` implementation for a given engine name.
"""

from __future__ import annotations

from rails.adapters.base import AgentSession
from rails.adapters.claude import ClaudeAdapter
from rails.adapters.codex import CodexAdapter
from rails.adapters.gemini import GeminiAdapter
from rails.config import RailsConfig

_ADAPTERS = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "gemini": GeminiAdapter,
}


def get_adapter(
    engine: str,
    cfg: RailsConfig,
    binary: list[str] | None = None,
    *,
    readonly: bool = False,
) -> AgentSession:
    try:
        adapter_cls = _ADAPTERS[engine]
    except KeyError as exc:
        raise ValueError(f"unknown engine: {engine!r}") from exc
    return adapter_cls(cfg, binary=binary, readonly=readonly)
