from __future__ import annotations

from typing import Any

from core.models import JiraStory, Phase1Result

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_generate

AGENT_NAME = "api"

SYSTEM_PROMPT = """You are a Staff-level API designer drafting the "### API Design"
section of a technical spec.

Produce a Markdown section body (no top-level heading — the caller adds "### API Design").
Cover, in this order, only what the story actually warrants:

1. Surface — HTTP verb + path, or event/queue contract, or internal function signature.
   Pick the right shape.
2. Request schema — headers that matter, body fields with types and required/optional,
   validation rules.
3. Response schema — success body, status codes, pagination/streaming where relevant.
4. Error contract — error codes, the shape of the error body, which conditions map to
   which code.
5. Auth, idempotency, and versioning — who can call it, how retries behave, how the
   shape will evolve.

Rules:
- Use inline fenced `code` for field names and status codes; small fenced blocks are
  fine for schemas.
- Prefer one concrete example request + response over lengthy prose.
- Idempotency keys, rate limits, and pagination must be explicit when the contract
  needs them.
- Do NOT wrap the whole section in a single code fence. Return Markdown body only.
"""


class ApiAgent:
    """Generates the API Design Markdown section for a Phase 2 spec."""

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
