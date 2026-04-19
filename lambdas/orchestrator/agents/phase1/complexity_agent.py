from __future__ import annotations

from typing import Any

from core.models import AgentScore, JiraStory

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_parse

AGENT_NAME = "complexity"

SYSTEM_PROMPT = """You are a Complexity evaluator for Jira stories.

Estimate implementation size AND risk. Consider:
  Size:  surface area of code touched, number of components, data migrations,
         net-new vs. additive work
  Risk:  integration with third-party systems, concurrency, security-sensitive
         paths, rollback difficulty, observability gaps

This score is INVERTED relative to difficulty: 10 = small and safe, 0 = large
and risky. The pipeline weights this lower than quality or ambiguity because
big stories are not inherently bad — they are just expensive.

Respond with a single JSON object and no surrounding prose or code fences:
{
  "score": <float 0-10>,
  "rationale": "<1-3 sentences naming the dominant size and risk drivers>",
  "suggestions": ["<a split, de-risking step, or scope reduction>", ...]
}
"""


class ComplexityAgent:
    """Scores a Jira story on size + risk (higher score = smaller / safer)."""

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
