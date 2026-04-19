from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.errors import AgentEvaluationError
from agents.phase1 import ambiguity_agent, base, complexity_agent, quality_agent
from core.models import JiraStory


def _story() -> JiraStory:
    return JiraStory(
        id="SPEC-1",
        title="Build a webhook receiver",
        description="As a user I want ...",
        acceptance_criteria=["Given X, When Y, Then Z"],
        story_points=3,
    )


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[_text_block(text)])


def _mock_client(body: object) -> AsyncMock:
    client = AsyncMock()
    if isinstance(body, Exception):
        client.messages.create.side_effect = body
    else:
        client.messages.create.return_value = body
    return client


@pytest.mark.asyncio
async def test_quality_agent_parses_valid_response():
    body = json.dumps(
        {"score": 8.5, "rationale": "Meets INVEST.", "suggestions": ["Split AC 2"]}
    )
    client = _mock_client(_response(body))
    agent = quality_agent.QualityAgent(client)

    result = await agent.evaluate(_story())

    assert result.agent_name == "quality"
    assert result.score == 8.5
    assert result.rationale == "Meets INVEST."
    assert result.suggestions == ["Split AC 2"]


@pytest.mark.asyncio
async def test_ambiguity_agent_parses_valid_response():
    body = json.dumps({"score": 7.0, "rationale": "Mostly clear.", "suggestions": []})
    agent = ambiguity_agent.AmbiguityAgent(_mock_client(_response(body)))
    result = await agent.evaluate(_story())
    assert result.agent_name == "ambiguity"
    assert result.score == 7.0


@pytest.mark.asyncio
async def test_complexity_agent_parses_valid_response():
    body = json.dumps({"score": 6.0, "rationale": "Medium risk.", "suggestions": ["De-risk auth"]})
    agent = complexity_agent.ComplexityAgent(_mock_client(_response(body)))
    result = await agent.evaluate(_story())
    assert result.agent_name == "complexity"
    assert result.score == 6.0


@pytest.mark.asyncio
async def test_invalid_json_raises_agent_evaluation_error():
    client = _mock_client(_response("not-json-at-all"))
    agent = quality_agent.QualityAgent(client)
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert exc_info.value.agent_name == "quality"
    assert "not valid JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_field_raises_agent_evaluation_error():
    body = json.dumps({"score": 8.0, "rationale": "..."})  # no 'suggestions'
    agent = quality_agent.QualityAgent(_mock_client(_response(body)))
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert "missing required field" in str(exc_info.value)


@pytest.mark.asyncio
async def test_out_of_range_score_raises_agent_evaluation_error():
    body = json.dumps({"score": 42.0, "rationale": "bad", "suggestions": []})
    agent = quality_agent.QualityAgent(_mock_client(_response(body)))
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert "schema validation failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_failure_wraps_in_agent_evaluation_error():
    client = _mock_client(RuntimeError("network down"))
    agent = quality_agent.QualityAgent(client)
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert "API call failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


@pytest.mark.asyncio
async def test_non_object_json_raises_agent_evaluation_error():
    body = json.dumps([1, 2, 3])
    agent = quality_agent.QualityAgent(_mock_client(_response(body)))
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert "not an object" in str(exc_info.value)


@pytest.mark.asyncio
async def test_response_with_no_text_blocks_raises():
    client = _mock_client(SimpleNamespace(content=[]))
    agent = quality_agent.QualityAgent(client)
    with pytest.raises(AgentEvaluationError) as exc_info:
        await agent.evaluate(_story())
    assert "no text blocks" in str(exc_info.value)


@pytest.mark.asyncio
async def test_agent_forwards_model_and_max_tokens_to_client():
    body = json.dumps({"score": 5.0, "rationale": "r", "suggestions": []})
    client = _mock_client(_response(body))
    agent = quality_agent.QualityAgent(client, model="custom-model", max_tokens=42)
    await agent.evaluate(_story())
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "custom-model"
    assert call_kwargs["max_tokens"] == 42
    assert call_kwargs["system"] == quality_agent.SYSTEM_PROMPT
    assert call_kwargs["messages"][0]["role"] == "user"


def test_each_agent_has_distinct_system_prompt():
    prompts = {
        quality_agent.SYSTEM_PROMPT,
        ambiguity_agent.SYSTEM_PROMPT,
        complexity_agent.SYSTEM_PROMPT,
    }
    assert len(prompts) == 3


def test_build_user_prompt_includes_story_fields():
    story = _story()
    prompt = base.build_user_prompt(story)
    assert story.id in prompt
    assert story.title in prompt
    assert story.description in prompt
    for ac in story.acceptance_criteria:
        assert ac in prompt


def test_build_user_prompt_handles_no_story_points():
    story = JiraStory(
        id="X-1",
        title="t",
        description="d",
        acceptance_criteria=[],
        story_points=None,
    )
    prompt = base.build_user_prompt(story)
    assert "unspecified" in prompt
    assert "(none)" in prompt
