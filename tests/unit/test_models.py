"""
Unit tests for lambdas/orchestrator/core/models.py.

Run with:
    uv run pytest tests/unit/test_models.py -v
"""

import pytest
from pydantic import ValidationError

from core.models import (
    AgentScore,
    JiraStory,
    Phase1Result,
    Phase2Result,
    SpecDocument,
    WebhookPayload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_score(**overrides) -> AgentScore:
    defaults = dict(
        agent_name="quality",
        score=7.0,
        rationale="Looks good.",
        suggestions=["Add more detail"],
    )
    defaults.update(overrides)
    return AgentScore(**defaults)


def _phase1(**overrides) -> Phase1Result:
    defaults = dict(
        quality=_agent_score(agent_name="quality"),
        ambiguity=_agent_score(agent_name="ambiguity"),
        complexity=_agent_score(agent_name="complexity"),
        composite_score=7.0,
        passed_gate=True,
    )
    defaults.update(overrides)
    return Phase1Result(**defaults)


def _phase2(**overrides) -> Phase2Result:
    defaults = dict(
        architecture="Monolith",
        api_design="REST",
        edge_cases="None identified",
        testing_strategy="Unit + integration",
    )
    defaults.update(overrides)
    return Phase2Result(**defaults)


# ---------------------------------------------------------------------------
# JiraStory tests
# ---------------------------------------------------------------------------

def test_jira_story_valid_no_story_points():
    story = JiraStory(
        id="PROJ-1",
        title="As a user I want X",
        description="Detailed description",
        acceptance_criteria=["AC1", "AC2"],
    )
    assert story.id == "PROJ-1"
    assert story.story_points is None


def test_jira_story_valid_with_story_points():
    story = JiraStory(
        id="PROJ-2",
        title="As a user I want Y",
        description="Another description",
        acceptance_criteria=["AC1"],
        story_points=5,
    )
    assert story.story_points == 5


# ---------------------------------------------------------------------------
# AgentScore tests
# ---------------------------------------------------------------------------

def test_agent_score_boundary_zero():
    s = _agent_score(score=0.0)
    assert s.score == 0.0


def test_agent_score_boundary_ten():
    s = _agent_score(score=10.0)
    assert s.score == 10.0


def test_agent_score_midpoint():
    s = _agent_score(score=5.5)
    assert s.score == 5.5


def test_agent_score_below_zero_raises():
    with pytest.raises(ValidationError) as exc_info:
        _agent_score(score=-0.1)
    assert "must be between 0 and 10" in str(exc_info.value)


def test_agent_score_above_ten_raises():
    with pytest.raises(ValidationError) as exc_info:
        _agent_score(score=10.1)
    assert "must be between 0 and 10" in str(exc_info.value)


def test_agent_score_string_coerces_to_float():
    # Pydantic v2 coerces "7" → 7.0 for a float field
    s = _agent_score(score="7")
    assert s.score == 7.0


# ---------------------------------------------------------------------------
# Phase1Result tests
# ---------------------------------------------------------------------------

def test_phase1_result_valid():
    p1 = _phase1()
    assert p1.composite_score == 7.0
    assert p1.passed_gate is True
    assert isinstance(p1.quality, AgentScore)
    assert isinstance(p1.ambiguity, AgentScore)
    assert isinstance(p1.complexity, AgentScore)


# ---------------------------------------------------------------------------
# Phase2Result tests
# ---------------------------------------------------------------------------

def test_phase2_result_valid():
    p2 = _phase2()
    assert p2.architecture == "Monolith"
    assert p2.api_design == "REST"
    assert p2.edge_cases == "None identified"
    assert p2.testing_strategy == "Unit + integration"


# ---------------------------------------------------------------------------
# SpecDocument tests
# ---------------------------------------------------------------------------

def test_spec_document_defaults():
    doc = SpecDocument(
        story_id="PROJ-1",
        phase1=_phase1(),
        spec_markdown="# Spec",
    )
    assert doc.phase2 is None
    assert doc.s3_key is None


def test_spec_document_fully_populated():
    doc = SpecDocument(
        story_id="PROJ-2",
        phase1=_phase1(),
        phase2=_phase2(),
        spec_markdown="# Full Spec",
        s3_key="specs/PROJ-2.md",
    )
    assert isinstance(doc.phase2, Phase2Result)
    assert doc.s3_key == "specs/PROJ-2.md"


# ---------------------------------------------------------------------------
# WebhookPayload tests
# ---------------------------------------------------------------------------

def test_webhook_payload_valid():
    payload = WebhookPayload(
        issue_key="PROJ-42",
        issue_summary="Build the thing",
        issue_description="We need to build the thing ASAP",
        project_key="PROJ",
    )
    assert payload.issue_key == "PROJ-42"
    assert payload.project_key == "PROJ"


def test_webhook_payload_missing_field_raises():
    with pytest.raises(ValidationError):
        WebhookPayload(
            issue_key="PROJ-42",
            # issue_summary intentionally omitted
            issue_description="desc",
            project_key="PROJ",
        )
