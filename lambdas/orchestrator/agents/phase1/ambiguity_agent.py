from __future__ import annotations

from typing import Any

from core.models import AgentScore, JiraStory

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_parse

AGENT_NAME = "ambiguity"

SYSTEM_PROMPT = """You are an Ambiguity evaluator for Jira stories.

Judge whether a competent engineer could implement this story without asking
clarifying questions. Penalize:
  - vague verbs ("improve", "optimize", "handle") without measurable targets
  - undefined nouns ("the data", "that flow") with no referent
  - acceptance criteria that describe desire instead of observable behavior
  - missing error/edge-case expectations that the story clearly depends on

Score the story 0-10 where 10 = fully unambiguous, 0 = unimplementable without rewrites.

Respond with a single JSON object and no surrounding prose or code fences:
{
  "score": <float 0-10>,
  "rationale": "<1-3 sentences naming the most load-bearing ambiguity, or why none exists>",
  "suggestions": ["<a clarifying question or concrete rewrite>", ...]
}
"""


class AmbiguityAgent:
    """Scores a Jira story on clarity / lack of ambiguity."""

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
