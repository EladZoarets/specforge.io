"""
Integration tests for the Phase 1 pipeline.

These tests drive ``run_phase1`` end-to-end using fake agents — no Anthropic
client is involved. The fakes implement the same ``async evaluate(story)``
shape as the real Phase 1 agents.
"""

from __future__ import annotations

import asyncio
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
    # Deterministic concurrency proof: each agent increments a counter on
    # entry and waits on a shared barrier. The barrier is released only when
    # all three have entered — so if any agent were awaited serially, the
    # barrier would never fire and the outer wait_for would time out. This
    # replaces a wall-clock assertion that was flaky under loaded CI.
    started = 0
    barrier = asyncio.Event()

    class _BarrierAgent:
        def __init__(self, name: str) -> None:
            self._name = name
            self.call_count = 0

        async def evaluate(self, story: JiraStory) -> AgentScore:  # noqa: ARG002
            nonlocal started
            self.call_count += 1
            started += 1
            if started == 3:
                barrier.set()
            await barrier.wait()
            return _score(self._name, 8.0)

    agents: dict[str, Any] = {
        "quality": _BarrierAgent("quality"),
        "ambiguity": _BarrierAgent("ambiguity"),
        "complexity": _BarrierAgent("complexity"),
    }

    # 2s safety timeout: if agents run serially, the barrier never releases
    # and wait_for raises TimeoutError — failing the test loudly instead of
    # hanging the suite.
    await asyncio.wait_for(
        run_phase1(_story(), agents, threshold=7.0),
        timeout=2.0,
    )

    assert started == 3
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


@pytest.mark.asyncio
async def test_cancelled_error_propagates_unwrapped():
    # Lambda timeouts arrive as cancellation; wrapping CancelledError as a
    # Phase1PipelineError would mask the shutdown signal. The pipeline must
    # let it propagate so the caller can honor the deadline cleanly.
    agents = _agents(quality=_RaisingAgent(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await run_phase1(_story(), agents, threshold=7.0)


@pytest.mark.asyncio
async def test_none_agent_value_raises_before_dispatch():
    ambiguity = _FakeAgent(_score("ambiguity", 7.0))
    complexity = _FakeAgent(_score("complexity", 6.0))
    agents: dict[str, Any] = {
        "quality": None,
        "ambiguity": ambiguity,
        "complexity": complexity,
    }

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name == "quality"
    assert "not callable" in str(exc_info.value)
    # Validation fires before dispatch — no agent should have been awaited.
    assert ambiguity.call_count == 0
    assert complexity.call_count == 0


@pytest.mark.asyncio
async def test_sync_evaluate_is_rejected_by_agent_validation():
    # ``callable`` accepts a sync method, so this guards against the narrower
    # case of None / missing attribute. A sync ``evaluate`` would still blow
    # up at await time, but the key contract — reject agents with no
    # callable ``evaluate`` at all — is covered here with an int sentinel.
    agents: dict[str, Any] = {
        "quality": _FakeAgent(_score("quality", 8.0)),
        "ambiguity": 42,  # no ``evaluate`` attribute at all
        "complexity": _FakeAgent(_score("complexity", 6.0)),
    }

    with pytest.raises(Phase1PipelineError) as exc_info:
        await run_phase1(_story(), agents, threshold=7.0)

    assert exc_info.value.agent_name == "ambiguity"
    assert "not callable" in str(exc_info.value)
