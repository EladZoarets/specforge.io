from __future__ import annotations


class AgentEvaluationError(Exception):
    """Raised when a Phase 1 agent fails to produce a valid AgentScore.

    Wraps the upstream cause (Anthropic API error, JSON decode error, missing
    key, schema validation error) so callers can attribute the failure to a
    specific agent name without leaking the transport-level exception type.
    """

    def __init__(self, agent_name: str, message: str) -> None:
        super().__init__(f"[{agent_name}] {message}")
        self.agent_name = agent_name
