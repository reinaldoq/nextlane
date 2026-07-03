"""Adapter registry: `get_adapter(engine, cfg, binary=None)` returns the
right `AgentSession` implementation for a given engine name.
"""

from __future__ import annotations

from rails.adapters.claude import ClaudeAdapter
from rails.config import RailsConfig

_ADAPTERS = {
    "claude": ClaudeAdapter,
}

_NOT_YET_IMPLEMENTED = ("codex", "gemini")


def get_adapter(engine: str, cfg: RailsConfig, binary: list[str] | None = None):
    if engine in _NOT_YET_IMPLEMENTED:
        raise NotImplementedError(f"{engine} adapter arrives in Task 3")
    try:
        adapter_cls = _ADAPTERS[engine]
    except KeyError as exc:
        raise ValueError(f"unknown engine: {engine!r}") from exc
    return adapter_cls(cfg, binary=binary)
