"""
Phase 2 pipeline: dispatch the four Phase 2 generation agents concurrently and
compose their Markdown sections into a Phase2Result.

Mirrors the Phase 1 pipeline's error-translation pattern: agent-layer
``AgentGenerationError`` is translated to pipeline-layer
``Phase2PipelineError`` with ``agent_name`` preserved. Non-``Exception``
``BaseException`` results (``CancelledError``, ``SystemExit``,
``KeyboardInterrupt``) propagate unwrapped so Lambda deadline cancellation
and process signals are honored.

The Phase 1 quality gate is checked BEFORE dispatch: a failed gate short-
circuits to ``None`` without awaiting any agent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.errors import AgentGenerationError
from core.models import JiraStory, Phase1Result, Phase2Result

_REQUIRED_AGENTS: tuple[str, ...] = ("architecture", "api", "edge_cases", "testing")


class Phase2PipelineError(Exception):
    """Raised when the Phase 2 pipeline cannot produce a Phase2Result.

    ``agent_name`` is the name of the single agent responsible for the failure,
    or ``None`` when the failure cannot be attributed to one agent (missing
    registration, unexpected exception type).
    """

    def __init__(self, agent_name: str | None, message: str) -> None:
        prefix = f"[{agent_name}]" if agent_name else "[phase2]"
        super().__init__(f"{prefix} {message}")
        self.agent_name = agent_name


async def run_phase2(
    story: JiraStory,
    phase1: Phase1Result,
    agents: dict[str, Any],
    threshold: float,
) -> Phase2Result | None:
    """Generate the four Phase 2 Markdown sections concurrently.

    If ``phase1.passed_gate`` is False (belt-and-suspenders: also check
    ``phase1.composite_score < threshold``), return ``None`` immediately
    without dispatching any agent.

    ``agents`` must contain keys ``"architecture"``, ``"api"``, ``"edge_cases"``,
    and ``"testing"``. Each value is expected to expose an ``async generate``
    method returning a Markdown string.

    Raises:
        Phase2PipelineError: if an agent is missing from ``agents``, if an agent
            has no callable ``generate`` attribute, or if any agent raises. The
            wrapped original exception is preserved as ``__cause__``.
    """
    # Gate check: the caller already computed passed_gate from the same
    # threshold, but we also recheck composite_score against threshold as a
    # belt-and-suspenders guard against a stale passed_gate flag.
    if not phase1.passed_gate or phase1.composite_score < threshold:
        return None

    for name in _REQUIRED_AGENTS:
        if name not in agents:
            raise Phase2PipelineError(None, f"missing agent: {name}")

    # Validate each agent has a callable ``generate`` attribute before we
    # dispatch. Otherwise, a None or misconfigured agent surfaces as a
    # confusing ``unexpected: AttributeError`` after the gather.
    for name in _REQUIRED_AGENTS:
        if not callable(getattr(agents[name], "generate", None)):
            raise Phase2PipelineError(
                name, "agent is not callable / missing generate method"
            )

    # gather with return_exceptions=True so one agent's failure doesn't cancel
    # the others — we need every result (or exception) to tag the right
    # agent_name on the wrapping error. ``return_exceptions=True`` captures
    # BaseException subclasses (CancelledError, SystemExit) as return values
    # rather than re-raising; we pass any non-``Exception`` BaseException
    # through unwrapped so shutdown / cancellation signals propagate.
    tasks = [agents[name].generate(story, phase1) for name in _REQUIRED_AGENTS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
        if isinstance(result, Exception):
            _raise_wrapped(result)

    architecture, api_design, edge_cases, testing_strategy = results  # type: ignore[misc]
    return Phase2Result(
        architecture=architecture,
        api_design=api_design,
        edge_cases=edge_cases,
        testing_strategy=testing_strategy,
    )


def _raise_wrapped(exc: Exception) -> None:
    """Translate an agent-layer exception into Phase2PipelineError."""
    if isinstance(exc, AgentGenerationError):
        raise Phase2PipelineError(exc.agent_name, str(exc)) from exc
    raise Phase2PipelineError(
        None, f"unexpected: {type(exc).__name__}"
    ) from exc
