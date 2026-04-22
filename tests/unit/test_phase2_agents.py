from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.errors import AgentEvaluationError, AgentGenerationError
from agents.phase2 import (
    api_agent,
    architecture_agent,
    base,
    edge_cases_agent,
    testing_agent,
)
from core.models import AgentScore, JiraStory, Phase1Result


def _story() -> JiraStory:
    return JiraStory(
        id="SPEC-7",
        title="Add idempotent webhook ingest",
        description="As an integrator I want replay-safe webhooks ...",
        acceptance_criteria=[
            "Given a duplicate signature, When POSTed, Then accepted exactly once",
            "Given a malformed body, When POSTed, Then 400 with a typed error",
        ],
        story_points=5,
    )


def _phase1() -> Phase1Result:
    return Phase1Result(
        quality=AgentScore(
            agent_name="quality",
            score=8.0,
            rationale="Solid INVEST coverage, mildly large.",
            suggestions=["Split the error-contract work into a sibling story"],
        ),
        ambiguity=AgentScore(
            agent_name="ambiguity",
            score=7.5,
            rationale="'Replay-safe' is load-bearing but not quantified.",
            suggestions=["Define the dedup window in minutes"],
        ),
        complexity=AgentScore(
            agent_name="complexity",
            score=6.0,
            rationale="Idempotency + HMAC interact non-trivially.",
            suggestions=[],
        ),
        composite_score=7.17,
        passed_gate=True,
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


AGENT_CASES = [
    ("architecture", architecture_agent.ArchitectureAgent, architecture_agent.AGENT_NAME),
    ("api", api_agent.ApiAgent, api_agent.AGENT_NAME),
    ("edge_cases", edge_cases_agent.EdgeCasesAgent, edge_cases_agent.AGENT_NAME),
    ("testing", testing_agent.TestingAgent, testing_agent.AGENT_NAME),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "cls", "name"), AGENT_CASES)
async def test_agent_returns_markdown_on_happy_path(label, cls, name):
    body = f"Some **markdown** body for {label}.\n\n- bullet one\n- bullet two\n"
    agent = cls(_mock_client(_response(body)))
    result = await agent.generate(_story(), _phase1())
    assert isinstance(result, str)
    assert result == body
    assert name  # module exposes a non-empty AGENT_NAME


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "cls", "name"), AGENT_CASES)
async def test_api_failure_wraps_in_agent_generation_error(label, cls, name):
    client = _mock_client(RuntimeError("network down"))
    agent = cls(client)
    with pytest.raises(AgentGenerationError) as exc_info:
        await agent.generate(_story(), _phase1())
    assert exc_info.value.agent_name == name
    assert "API call failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "cls", "name"), AGENT_CASES)
async def test_no_text_blocks_raises_agent_generation_error(label, cls, name):
    client = _mock_client(SimpleNamespace(content=[]))
    agent = cls(client)
    with pytest.raises(AgentGenerationError) as exc_info:
        await agent.generate(_story(), _phase1())
    assert exc_info.value.agent_name == name
    assert "no text blocks" in str(exc_info.value)


@pytest.mark.asyncio
async def test_empty_text_raises_agent_generation_error():
    agent = architecture_agent.ArchitectureAgent(_mock_client(_response("   \n  ")))
    with pytest.raises(AgentGenerationError) as exc_info:
        await agent.generate(_story(), _phase1())
    assert "empty" in str(exc_info.value)


def test_each_agent_has_distinct_system_prompt():
    prompts = {
        architecture_agent.SYSTEM_PROMPT,
        api_agent.SYSTEM_PROMPT,
        edge_cases_agent.SYSTEM_PROMPT,
        testing_agent.SYSTEM_PROMPT,
    }
    assert len(prompts) == 4


def test_agent_generation_error_is_distinct_from_evaluation_error():
    assert AgentGenerationError is not AgentEvaluationError
    assert not issubclass(AgentGenerationError, AgentEvaluationError)
    assert not issubclass(AgentEvaluationError, AgentGenerationError)
    # Same constructor shape: (agent_name, message)
    err = AgentGenerationError("architecture", "boom")
    assert err.agent_name == "architecture"
    assert "[architecture]" in str(err)
    assert "boom" in str(err)


@pytest.mark.asyncio
async def test_agent_forwards_model_and_max_tokens_to_client():
    body = "body"
    client = _mock_client(_response(body))
    agent = architecture_agent.ArchitectureAgent(client, model="custom-model", max_tokens=2048)
    await agent.generate(_story(), _phase1())
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "custom-model"
    assert call_kwargs["max_tokens"] == 2048
    assert call_kwargs["system"] == architecture_agent.SYSTEM_PROMPT
    assert call_kwargs["messages"][0]["role"] == "user"
    # User prompt carries both story and phase1 context
    user_content = call_kwargs["messages"][0]["content"]
    assert "SPEC-7" in user_content
    assert "Composite Score" in user_content


def test_build_user_prompt_includes_story_and_phase1():
    story = _story()
    phase1 = _phase1()
    prompt = base.build_user_prompt(story, phase1)
    assert story.id in prompt
    assert story.title in prompt
    assert story.description in prompt
    for ac in story.acceptance_criteria:
        assert ac in prompt
    assert "Composite Score" in prompt
    assert "7.17" in prompt
    assert phase1.quality.rationale in prompt
    assert phase1.ambiguity.rationale in prompt
    assert phase1.complexity.rationale in prompt
    # Suggestions from each phase1 agent surface for the generator
    assert "dedup window" in prompt


def test_build_user_prompt_handles_no_story_points_and_no_suggestions():
    story = JiraStory(
        id="X-1",
        title="t",
        description="d",
        acceptance_criteria=[],
        story_points=None,
    )
    phase1 = Phase1Result(
        quality=AgentScore(agent_name="quality", score=9.0, rationale="r", suggestions=[]),
        ambiguity=AgentScore(agent_name="ambiguity", score=9.0, rationale="r", suggestions=[]),
        complexity=AgentScore(agent_name="complexity", score=9.0, rationale="r", suggestions=[]),
        composite_score=9.0,
        passed_gate=True,
    )
    prompt = base.build_user_prompt(story, phase1)
    assert "unspecified" in prompt
    assert "(none)" in prompt
    assert "Suggestions: (none)" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "cls", "name"), AGENT_CASES)
async def test_generate_signature_returns_str(label, cls, name):
    body = "markdown"
    agent = cls(_mock_client(_response(body)))
    result = await agent.generate(story=_story(), phase1=_phase1())
    assert isinstance(result, str)
