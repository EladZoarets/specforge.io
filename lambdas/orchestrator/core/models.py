"""
Core Pydantic v2 models for the specforge.io orchestrator pipeline.

These are plain mutable data containers. No computed fields — composite_score
and passed_gate are set by the pipeline layer after evaluation.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class JiraStory(BaseModel):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    story_points: int | None = None


class AgentScore(BaseModel):
    agent_name: str
    score: float
    rationale: str
    suggestions: list[str]

    @field_validator("score", mode="before")
    @classmethod
    def validate_score_range(cls, value: object) -> object:
        # Coerce to float so we can range-check; Pydantic will finish the
        # coercion after we return the raw value.
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            # Let Pydantic produce the type-error message.
            return value
        if not (0.0 <= numeric <= 10.0):
            raise ValueError(
                f"score must be between 0 and 10, got {numeric}"
            )
        return value


class Phase1Result(BaseModel):
    quality: AgentScore
    ambiguity: AgentScore
    complexity: AgentScore
    composite_score: float
    passed_gate: bool


class Phase2Result(BaseModel):
    architecture: str
    api_design: str
    edge_cases: str
    testing_strategy: str


class SpecDocument(BaseModel):
    story_id: str
    phase1: Phase1Result
    phase2: Phase2Result | None = None
    spec_markdown: str
    s3_key: str | None = None


class WebhookPayload(BaseModel):
    issue_key: str
    issue_summary: str
    issue_description: str
    project_key: str
