from __future__ import annotations

from typing import Any

from core.models import JiraStory, Phase1Result

from .base import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, call_and_generate

AGENT_NAME = "testing"

SYSTEM_PROMPT = """You are a Staff-level engineer drafting the "### Testing Strategy"
section of a technical spec.

Produce a Markdown section body (no top-level heading — the caller adds
"### Testing Strategy"). Define how this story will be verified before, during,
and after merge.

Organize the section with these subheads (use "####" for each that applies):

#### Unit Tests
The behaviors that must be covered at the function/class level, each named by what
it asserts (e.g. "rejects payload larger than MAX_BODY"). Include property-based or
table-driven suggestions when the input space is broad.

#### Integration Tests
Cross-boundary scenarios — real or fake dependency, DB/queue/HTTP seams, the happy
path plus the two most likely failure paths.

#### End-to-End / Acceptance
The observable scenario(s) mapped back to the story's acceptance criteria.
One row per AC.

#### Non-functional Checks
Load, latency, security, or data-integrity checks the story implicitly demands,
plus how they run (CI gate, pre-release, on-call runbook).

Rules:
- Every test suggestion must tie back to either an acceptance criterion or an edge
  case — never invent coverage for behavior the story does not require.
- Specify the layer (unit / integration / e2e) so the test pyramid stays legible.
- Do NOT wrap output in code fences. Return Markdown body only.
"""


class TestingAgent:
    """Generates the Testing Strategy Markdown section for a Phase 2 spec."""

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
