from __future__ import annotations

from typing import Any

from core.models import JiraStory, Phase1Result

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_generate

AGENT_NAME = "edge_cases"

SYSTEM_PROMPT = """You are a Staff-level engineer drafting the "### Edge Cases" section
of a technical spec.

Produce a Markdown section body (no top-level heading — the caller adds "### Edge Cases").
Think like a reviewer who has seen production break. Enumerate the failure modes this
story must handle, grouped by category. Use only the categories that apply:

- Input boundaries — empty, oversized, null, unicode, duplicates, ordering, race
  conditions at the edge.
- Upstream/downstream failure — timeouts, partial results, poison messages, schema
  drift in a dependency.
- Concurrency — simultaneous writes, replay, at-least-once delivery, stale caches.
- Persistence — partial writes, idempotency loss, migration-in-flight, clock skew.
- Authorization — missing/expired creds, privilege escalation through a legitimate field.
- Observability — cases where a silent failure would be indistinguishable from success.

For each edge case, give: a one-line scenario, the observable symptom, and the required
behavior. Use a Markdown bulleted or table layout — pick whichever is more scannable
for the count.

Rules:
- Be specific to THIS story. Generic "handle errors gracefully" bullets are failures.
- Rank top-down by blast radius, not by order of discovery.
- Do NOT wrap output in code fences. Return Markdown body only.
"""


class EdgeCasesAgent:
    """Generates the Edge Cases Markdown section for a Phase 2 spec."""

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
