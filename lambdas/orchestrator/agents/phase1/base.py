from __future__ import annotations

import json
from typing import Any

from core.models import AgentScore, JiraStory
from pydantic import ValidationError

from ..errors import AgentEvaluationError

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024


def build_user_prompt(story: JiraStory) -> str:
    """Serialize a Jira story into the user-turn prompt shared by all Phase 1 agents.

    Output format is stable JSON so prompt tests can assert on it.
    """
    acceptance = "\n".join(f"- {item}" for item in story.acceptance_criteria) or "- (none)"
    points = story.story_points if story.story_points is not None else "unspecified"
    return (
        f"Story ID: {story.id}\n"
        f"Title: {story.title}\n"
        f"Story Points: {points}\n\n"
        f"Description:\n{story.description}\n\n"
        f"Acceptance Criteria:\n{acceptance}\n\n"
        'Return a single JSON object: {"score": float 0-10, "rationale": str, '
        '"suggestions": [str, ...]}. Do not wrap in code fences.'
    )


def _extract_text(response: Any) -> str:
    try:
        blocks = response.content
        for block in blocks:
            # anthropic SDK uses .type == "text" for text blocks
            if getattr(block, "type", None) == "text":
                return block.text
        # Fallback: first block with a .text attribute
        for block in blocks:
            if hasattr(block, "text"):
                return block.text
    except (AttributeError, TypeError, IndexError) as exc:
        raise ValueError(f"unexpected response shape: {exc}") from exc
    raise ValueError("response contained no text blocks")


async def call_and_parse(
    *,
    client: Any,
    model: str,
    system_prompt: str,
    agent_name: str,
    story: JiraStory,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> AgentScore:
    """Issue a single Anthropic request, parse the JSON body, and return an AgentScore.

    Any failure in that chain (transport, JSON, schema, shape) is re-raised as
    AgentEvaluationError so callers have a single exception type to handle.
    """
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": build_user_prompt(story)}],
        )
    except Exception as exc:  # noqa: BLE001 — transport failures are opaque by design
        raise AgentEvaluationError(agent_name, f"API call failed: {exc}") from exc

    try:
        text = _extract_text(response)
    except ValueError as exc:
        raise AgentEvaluationError(agent_name, str(exc)) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentEvaluationError(agent_name, f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AgentEvaluationError(
            agent_name, f"response JSON was not an object: {type(data).__name__}"
        )

    try:
        return AgentScore(
            agent_name=agent_name,
            score=data["score"],
            rationale=data["rationale"],
            suggestions=data["suggestions"],
        )
    except KeyError as exc:
        raise AgentEvaluationError(agent_name, f"missing required field: {exc}") from exc
    except ValidationError as exc:
        raise AgentEvaluationError(agent_name, f"schema validation failed: {exc}") from exc
