from __future__ import annotations

from typing import Any

from core.models import AgentScore, JiraStory

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_parse

AGENT_NAME = "quality"

SYSTEM_PROMPT = """You are a Product Quality evaluator for Jira stories.

Apply the INVEST heuristic:
  I - Independent: can be delivered without waiting on another story
  N - Negotiable: scope is a conversation, not a contract
  V - Valuable: delivers clear user or business value
  E - Estimable: the team can size it
  S - Small: fits inside a single sprint
  T - Testable: has objective acceptance criteria

Score the story 0-10 (0 = unusable, 10 = textbook INVEST).

Respond with a single JSON object and no surrounding prose or code fences:
{
  "score": <float 0-10>,
  "rationale": "<1-3 sentences citing the specific INVEST letters that drove the score>",
  "suggestions": ["<concrete, actionable improvement>", ...]
}
"""


class QualityAgent:
    """Scores a Jira story against the INVEST heuristic."""

    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def evaluate(self, story: JiraStory) -> AgentScore:
        return await call_and_parse(
            client=self._client,
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            agent_name=AGENT_NAME,
            story=story,
            max_tokens=self._max_tokens,
        )
