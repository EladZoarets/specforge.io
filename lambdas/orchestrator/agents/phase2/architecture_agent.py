from __future__ import annotations

from typing import Any

from core.models import JiraStory, Phase1Result

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_generate

AGENT_NAME = "architecture"

SYSTEM_PROMPT = """You are a Staff-level Software Architect drafting the "### Architecture"
section of a technical spec.

Produce a Markdown section body (no top-level heading — the caller adds "### Architecture").
Cover, in this order, only what the story actually warrants:

1. Component overview — which services/modules/lambdas participate and how responsibility
   is split.
2. Data flow — the critical path from input to persisted outcome, plus any out-of-band
   side effects.
3. Data model touchpoints — tables/collections/queues read or written (schema-level,
   not field-level).
4. Non-functional constraints — latency, throughput, consistency, cost ceilings that
   shape the design.
5. Key trade-offs — the 1-3 decisions another engineer would challenge in review,
   with the chosen side.

Rules:
- Be concrete. Name components, not "a service".
- No ASCII diagrams unless they clarify something text cannot.
- If the Phase 1 evaluation flagged ambiguity, call out the design assumption you had to make.
- Do NOT wrap output in code fences. Return Markdown body only.
"""


class ArchitectureAgent:
    """Generates the Architecture Markdown section for a Phase 2 spec."""

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

    async def generate(self, story: JiraStory, phase1: Phase1Result) -> str:
        return await call_and_generate(
            client=self._client,
            model=self._model,
            system_prompt=SYSTEM_PROMPT,
            agent_name=AGENT_NAME,
            story=story,
            phase1=phase1,
            max_tokens=self._max_tokens,
        )
