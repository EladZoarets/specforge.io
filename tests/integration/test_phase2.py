"""
Integration tests for the Phase 2 pipeline.

These tests drive ``run_phase2`` end-to-end using fake agents — no Anthropic
client is involved. The fakes implement the same ``async generate(story,
phase1)`` shape as the real Phase 2 agents.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agents.errors import AgentGenerationError
from core.models import AgentScore, JiraStory, Phase1Result
from pipeline.phase2 import Phase2PipelineError, run_phase2


def _story() -> JiraStory:
    return JiraStory(
        id="SPEC-1",
        title="Build a webhook receiver",
        description="As a user I want ...",
        acceptance_criteria=["Given X, When Y, Then Z"],
        story_points=3,
    )


def _score(name: str, value: float) -> AgentScore:
    return AgentScore(
        agent_name=name,
        score=value,
        rationale=f"{name} rationale",
        suggestions=[],
    )


def _phase1(passed: bool = True, composite: float = 8.0) -> Phase1Result:
    return Phase1Result(
        quality=_score("quality", 8.0),
        ambiguity=_score("ambiguity", 8.0),
        complexity=_score("complexity", 8.0),
        composite_score=composite,
        passed_gate=passed,
    )


class _FakeAgent:
    """Returns a canned Markdown string from ``generate``."""

    def __init__(self, markdown: str) -> None:
        self._markdown = markdown
        self.call_count = 0

    async def generate(
        self,
        story: JiraStory,  # noqa: ARG002
        phase1: Phase1Result,  # noqa: ARG002
    ) -> str:
        self.call_count += 1
        return self._markdown


class _RaisingAgent:
    """Raises the given exception on ``generate``."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.call_count = 0

    async def generate(
        self,
        story: JiraStory,  # noqa: ARG002
        phase1: Phase1Result,  # noqa: ARG002
    ) -> str:
        self.call_count += 1
        raise self._exc


def _agents(
    architecture: Any | None = None,
    api: Any | None = None,
    edge_cases: Any | None = None,
    testing: Any | None = None,
) -> dict[str, Any]:
    return {
        "architecture": architecture or _FakeAgent("# Architecture body"),
        "api": api or _FakeAgent("# API body"),
        "edge_cases": edge_cases or _FakeAgent("# Edge cases body"),
        "testing": testing or _FakeAgent("# Testing body"),
    }


@pytest.mark.asyncio
async def test_returns_none_when_gate_failed_and_dispatches_no_agents():
    agents = _agents()
    result = await run_phase2(_story(), _phase1(passed=False, composite=5.0), agents, 7.0)

    assert result is None
    for agent in agents.values():
        assert agent.call_count == 0


@pytest.mark.asyncio
async def test_happy_path_populates_each_section():
    agents = _agents(
        architecture=_FakeAgent("arch md"),
        api=_FakeAgent("api md"),
        edge_cases=_FakeAgent("edges md"),
        testing=_FakeAgent("test md"),
    )
    result = await run_phase2(_story(), _phase1(), agents, 7.0)

    assert result is not None
    assert result.architecture == "arch md"
    assert result.api_design == "api md"
    assert result.edge_cases == "edges md"
    assert result.testing_strategy == "test md"


@pytest.mark.asyncio
async def test_agent_generation_error_is_wrapped_with_agent_name():
    cause = AgentGenerationError("api", "empty response body")
    agents = _agents(api=_RaisingAgent(cause))

    with pytest.raises(Phase2PipelineError) as exc_info:
        await run_phase2(_story(), _phase1(), agents, 7.0)

    assert exc_info.value.agent_name == "api"
    assert "empty response body" in str(exc_info.value)
    assert exc_info.value.__cause__ is cause


@pytest.mark.asyncio
async def test_missing_agent_raises_before_dispatch():
    architecture = _FakeAgent("arch md")
    api = _FakeAgent("api md")
    agents: dict[str, Any] = {
        "architecture": architecture,
        "api": api,
        # edge_cases + testing missing
    }

    with pytest.raises(Phase2PipelineError) as exc_info:
        await run_phase2(_story(), _phase1(), agents, 7.0)

    assert exc_info.value.agent_name is None
    assert "missing agent" in str(exc_info.value)
    assert architecture.call_count == 0
    assert api.call_count == 0


@pytest.mark.asyncio
async def test_non_callable_generate_raises_before_dispatch():
    architecture = _FakeAgent("arch md")
    edge_cases = _FakeAgent("edges md")
    testing = _FakeAgent("test md")
    agents: dict[str, Any] = {
        "architecture": architecture,
        "api": 42,  # no ``generate`` attribute at all
        "edge_cases": edge_cases,
        "testing": testing,
    }

    with pytest.raises(Phase2PipelineError) as exc_info:
        await run_phase2(_story(), _phase1(), agents, 7.0)

    assert exc_info.value.agent_name == "api"
    assert "not callable" in str(exc_info.value)
    # Validation fires before dispatch — no agent should have been awaited.
    assert architecture.call_count == 0
    assert edge_cases.call_count == 0
    assert testing.call_count == 0


@pytest.mark.asyncio
async def test_unexpected_exception_is_wrapped():
    cause = RuntimeError("boom")
    agents = _agents(edge_cases=_RaisingAgent(cause))

    with pytest.raises(Phase2PipelineError) as exc_info:
        await run_phase2(_story(), _phase1(), agents, 7.0)

    assert exc_info.value.agent_name is None
    assert "unexpected: RuntimeError" in str(exc_info.value)
    assert exc_info.value.__cause__ is cause


@pytest.mark.asyncio
async def test_cancelled_error_propagates_unwrapped():
    # Lambda timeouts arrive as cancellation; wrapping CancelledError as a
    # Phase2PipelineError would mask the shutdown signal.
    agents = _agents(testing=_RaisingAgent(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await run_phase2(_story(), _phase1(), agents, 7.0)


@pytest.mark.asyncio
async def test_belt_and_suspenders_composite_below_threshold_returns_none():
    # If passed_gate is stale-True but composite is actually below threshold,
    # the second guard still skips dispatch.
    agents = _agents()
    phase1 = _phase1(passed=True, composite=5.0)

    result = await run_phase2(_story(), phase1, agents, 7.0)

    assert result is None
    for agent in agents.values():
        assert agent.call_count == 0
