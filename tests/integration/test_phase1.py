"""
Integration tests for the Phase 1 pipeline.

These tests drive ``run_phase1`` end-to-end using fake agents — no Anthropic
client is involved. The fakes implement the same ``async evaluate(story)``
shape as the real Phase 1 agents.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from agents.errors import AgentEvaluationError
from core.models import AgentScore, JiraStory
from pipeline.phase1 import Phase1PipelineError, run_phase1


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


class _FakeAgent:
    """Returns a canned AgentScore after an optional sleep."""

    def __init__(self, score: AgentScore, *, sleep: float = 0.0) -> None:
        self._score = score
        self._sleep = sleep
        self.call_count = 0

    async def evaluate(self, story: JiraStory) -> AgentScore:  # noqa: ARG002
        self.call_count += 1
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return self._score


class _RaisingAgent:
    """Raises the given exception on evaluate."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.call_count = 0

    async def evaluate(self, story: JiraStory) -> AgentScore:  # noqa: ARG002
        self.call_count += 1
        raise self._exc


def _agents(
    quality: Any | None = None,
    ambiguity: Any | None = None,
    complexity: Any | None = None,
) -> dict[str, Any]:
    return {
        "quality": quality or _FakeAgent(_score("quality", 8.0)),
        "ambiguity": ambiguity or _FakeAgent(_score("ambiguity", 7.0)),
        "complexity": complexity or _FakeAgent(_score("complexity", 6.0)),
    }


@pytest.mark.asyncio
async def test_all_pass_happy_path():
    # 8*0.4 + 7*0.35 + 6*0.25 = 3.2 + 2.45 + 1.5 = 7.15
    result = await run_phase1(_story(), _agents(), threshold=7.0)

    assert result.quality.score == 8.0
    assert result.ambiguity.score == 7.0
    assert result.complexity.score == 6.0
    assert result.composite_score == 7.15
    assert result.passed_gate is True


@pytest.mark.asyncio
async def test_agent_evaluation_error_is_wrapped_with_agent_name():
    cause = AgentEvaluationError("ambiguity", "schema validation failed")
    agents = _agents(ambiguity=_RaisingAgent(cause))

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name == "ambiguity"
    assert "schema validation failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is cause


@pytest.mark.asyncio
async def test_timeout_is_wrapped_with_none_agent_name():
    agents = _agents(quality=_RaisingAgent(TimeoutError()))

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name is None
    assert "timeout" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, TimeoutError)


@pytest.mark.asyncio
async def test_gate_boundary_just_above_passes():
    # composite = 7.00 exactly; evaluate_gate uses >=, so passed_gate=True.
    agents = _agents(
        quality=_FakeAgent(_score("quality", 7.0)),
        ambiguity=_FakeAgent(_score("ambiguity", 7.0)),
        complexity=_FakeAgent(_score("complexity", 7.0)),
    )
    result = await run_phase1(_story(), agents, threshold=7.0)

    assert result.composite_score == 7.0
    assert result.passed_gate is True


@pytest.mark.asyncio
async def test_gate_boundary_just_below_fails():
    # 7*0.4 + 7*0.35 + 6*0.25 = 2.8 + 2.45 + 1.5 = 6.75
    agents = _agents(
        quality=_FakeAgent(_score("quality", 7.0)),
        ambiguity=_FakeAgent(_score("ambiguity", 7.0)),
        complexity=_FakeAgent(_score("complexity", 6.0)),
    )
    result = await run_phase1(_story(), agents, threshold=7.0)

    assert result.composite_score == 6.75
    assert result.passed_gate is False


@pytest.mark.asyncio
async def test_missing_agent_raises_without_calling_any_agent():
    quality = _FakeAgent(_score("quality", 8.0))
    agents = {"quality": quality}  # ambiguity + complexity missing

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name is None
    assert "missing agent" in str(exc_info.value)
    # short-circuit before dispatch
    assert quality.call_count == 0


@pytest.mark.asyncio
async def test_empty_agents_dict_raises():
    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), {}, threshold=7.0)

    assert "missing agent" in str(exc_info.value)


@pytest.mark.asyncio
async def test_agents_run_concurrently():
    # Three 100ms sleeps serialized = 300ms; concurrent should be ~100ms.
    # We assert "well under sum" to tolerate event-loop jitter.
    sleep = 0.1
    agents = _agents(
        quality=_FakeAgent(_score("quality", 8.0), sleep=sleep),
        ambiguity=_FakeAgent(_score("ambiguity", 7.0), sleep=sleep),
        complexity=_FakeAgent(_score("complexity", 6.0), sleep=sleep),
    )

    start = time.perf_counter()
    await run_phase1(_story(), agents, threshold=7.0)
    elapsed = time.perf_counter() - start

    # Serial would be ~0.30s; concurrent ~0.10s. 0.20s gives comfortable margin.
    assert elapsed < 0.2, f"expected concurrent execution, took {elapsed:.3f}s"
    for agent in agents.values():
        assert agent.call_count == 1


@pytest.mark.asyncio
async def test_unexpected_exception_is_wrapped():
    cause = RuntimeError("boom")
    agents = _agents(complexity=_RaisingAgent(cause))

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name is None
    assert "unexpected: RuntimeError" in str(exc_info.value)
    assert exc_info.value.__cause__ is cause
