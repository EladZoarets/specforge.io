from __future__ import annotations

from typing import Any


class AgentRegistry:
    """A simple name→agent registry.

    Agents are duck-typed — any object that satisfies the call site's
    expected interface (e.g. ``async def evaluate(story)``) can be registered.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    def register(self, name: str, agent: Any) -> None:
        self._agents[name] = agent

    def get(self, name: str) -> Any:
        if name not in self._agents:
            raise KeyError(f"agent {name!r} is not registered")
        return self._agents[name]

    def names(self) -> list[str]:
        return list(self._agents.keys())

    def __contains__(self, name: object) -> bool:
        return name in self._agents
