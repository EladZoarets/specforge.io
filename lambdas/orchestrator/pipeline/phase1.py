"""
Phase 1 pipeline: run quality / ambiguity / complexity agents concurrently and
compose their AgentScore results into a Phase1Result.

The pipeline layer owns error translation (agent-layer AgentEvaluationError →
pipeline-layer Phase1PipelineError) and composite-score gating. Timeouts are
the caller's concern; this module does not impose its own deadline.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.errors import AgentEvaluationError
from core.models import JiraStory, Phase1Result
from core.scoring import build_phase1_result

_REQUIRED_AGENTS: tuple[str, ...] = ("quality", "ambiguity", "complexity")


class Phase1PipelineError(Exception):
    """Raised when the Phase 1 pipeline cannot produce a Phase1Result.

    ``agent_name`` is the name of the single agent responsible for the failure,
    or ``None`` when the failure cannot be attributed to one agent (missing
    registration, timeout, or unexpected exception type).
    """

    def __init__(self, agent_name: str | None, message: str) -> None:
        prefix = f"[{agent_name}]" if agent_name else "[phase1]"
        super().__init__(f"{prefix} {message}")
        self.agent_name = agent_name


async def run_phase1(
    story: JiraStory,
    agents: dict[str, Any],
    threshold: float,
) -> Phase1Result:
    """Evaluate ``story`` with the three Phase 1 agents concurrently.

    ``agents`` must contain keys ``"quality"``, ``"ambiguity"``, and
    ``"complexity"``. Each value is expected to expose an ``async evaluate``
    method returning an ``AgentScore``.

    Raises:
        Phase1PipelineError: if an agent is missing from ``agents``, if any
            agent raises, or if any agent times out. The wrapped original
            exception is preserved as ``__cause__`` when applicable.
    """
    for name in _REQUIRED_AGENTS:
        if name not in agents:
            raise Phase1PipelineError(None, f"missing agent: {name}")

    # Validate each agent has a callable ``evaluate`` attribute before we
    # dispatch. Otherwise, a None or misconfigured agent surfaces as a
    # confusing ``unexpected: AttributeError`` after the gather.
    for name in _REQUIRED_AGENTS:
        if not callable(getattr(agents[name], "evaluate", None)):
            raise Phase1PipelineError(
                name, "agent is not callable / missing evaluate method"
            )

    # gather with return_exceptions=True so one agent's failure doesn't cancel
    # the others — we need every result (or exception) to tag the right
    # agent_name on the wrapping error. Note: ``return_exceptions=True`` will
    # capture ``BaseException`` subclasses (including ``CancelledError``) as
    # return values rather than re-raising. We pass any non-``Exception``
    # BaseException through unwrapped so SystemExit, KeyboardInterrupt, and
    # CancelledError propagate — a Lambda timeout sends cancellation, and
    # swallowing it as a Phase1PipelineError would mask the real cause and
    # break graceful shutdown.
    tasks = [agents[name].evaluate(story) for name in _REQUIRED_AGENTS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
        if isinstance(result, Exception):
            _raise_wrapped(result)

    quality, ambiguity, complexity = results  # type: ignore[misc]
    return build_phase1_result(quality, ambiguity, complexity, threshold)


def _raise_wrapped(exc: Exception) -> None:
    """Translate an agent-layer exception into Phase1PipelineError."""
    if isinstance(exc, AgentEvaluationError):
        raise Phase1PipelineError(exc.agent_name, str(exc)) from exc
    if isinstance(exc, asyncio.TimeoutError):
        raise Phase1PipelineError(None, "timeout") from exc
    raise Phase1PipelineError(
        None, f"unexpected: {type(exc).__name__}"
    ) from exc
