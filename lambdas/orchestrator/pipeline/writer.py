"""
Spec writer: assemble a complete Markdown spec document from a story, its
Phase 1 evaluation, and (optionally) the Phase 2 generation output.

``assemble_spec`` is a pure function — no I/O, no logging, no clock reads.
Same inputs produce byte-identical output, which is what makes the resulting
document safe to S3-upload with content-addressable keys downstream.

Section order is fixed:
    1. Story
    2. Evaluation Summary
    3. Architecture
    4. API Design
    5. Implementation Steps
    6. Edge Cases
    7. Testing Strategy
    8. Definition of Done

When Phase 2 was skipped (gate failed), sections 3-7 contain a short
gate-fail block that explains why Phase 2 was skipped and surfaces the top
1-2 Phase 1 suggestions so the reader knows what to fix. Sections 1, 2, and
8 are always rendered from the inputs alone.
"""

from __future__ import annotations

from core.models import JiraStory, Phase1Result, Phase2Result

# Canonical Definition of Done — writer-owned, not agent-generated. This
# stays constant across all specs so reviewers see a predictable checklist.
_DEFINITION_OF_DONE: tuple[str, ...] = (
    "Unit tests pass",
    "Integration tests pass",
    "Code reviewed",
    "Documentation updated",
    "Deployed to dev environment",
    "Acceptance criteria verified by PM",
)


def assemble_spec(
    story: JiraStory,
    phase1: Phase1Result,
    phase2: Phase2Result | None,
) -> str:
    """Render the full Markdown spec document.

    Pure function: deterministic, no I/O. ``phase2 is None`` signals the
    Phase 1 quality gate failed — sections 3-7 render a gate-fail block
    instead of agent-generated content.
    """
    sections: list[str] = [
        _render_story(story),
        _render_evaluation_summary(phase1),
    ]

    if phase2 is None:
        gate_fail_block = _render_gate_fail_block(phase1)
        sections.extend(
            [
                _wrap_section("Architecture", gate_fail_block),
                _wrap_section("API Design", gate_fail_block),
                _wrap_section("Implementation Steps", gate_fail_block),
                _wrap_section("Edge Cases", gate_fail_block),
                _wrap_section("Testing Strategy", gate_fail_block),
            ]
        )
    else:
        sections.extend(
            [
                _wrap_section("Architecture", phase2.architecture),
                _wrap_section("API Design", phase2.api_design),
                _render_implementation_steps(story, phase1),
                _wrap_section("Edge Cases", phase2.edge_cases),
                _wrap_section("Testing Strategy", phase2.testing_strategy),
            ]
        )

    sections.append(_render_definition_of_done())

    # Two blank lines between sections so they render as distinct blocks
    # and the resulting Markdown diffs cleanly.
    return "\n\n".join(sections) + "\n"


def _wrap_section(heading: str, body: str) -> str:
    """Render ``## heading`` followed by ``body`` with a blank line between."""
    return f"## {heading}\n\n{body.rstrip()}"


def _render_story(story: JiraStory) -> str:
    points = str(story.story_points) if story.story_points is not None else "unspecified"

    if story.acceptance_criteria:
        ac_lines = "\n".join(
            f"{idx}. {ac}" for idx, ac in enumerate(story.acceptance_criteria, start=1)
        )
    else:
        ac_lines = "_No acceptance criteria provided._"

    body = (
        f"**ID:** {story.id}\n\n"
        f"**Title:** {story.title}\n\n"
        f"**Description:**\n\n{story.description}\n\n"
        f"**Acceptance Criteria:**\n\n{ac_lines}\n\n"
        f"**Story Points:** {points}"
    )
    return _wrap_section("Story", body)


def _render_evaluation_summary(phase1: Phase1Result) -> str:
    rows = [
        ("Quality", phase1.quality),
        ("Ambiguity", phase1.ambiguity),
        ("Complexity", phase1.complexity),
    ]

    table_lines = [
        "| Agent | Score | Rationale |",
        "| --- | --- | --- |",
    ]
    for label, agent_score in rows:
        # Rationale is free-text from an LLM; strip pipes/newlines so we
        # don't break the Markdown table layout.
        rationale = agent_score.rationale.replace("|", "\\|").replace("\n", " ")
        table_lines.append(
            f"| {label} | {agent_score.score:.2f} | {rationale} |"
        )

    body = (
        "\n".join(table_lines)
        + f"\n\n**Composite Score:** {phase1.composite_score:.2f}"
        + f"\n\n**Passed Gate:** {phase1.passed_gate}"
    )
    return _wrap_section("Evaluation Summary", body)


def _render_implementation_steps(story: JiraStory, phase1: Phase1Result) -> str:
    if story.acceptance_criteria:
        step_lines = [
            f"{idx}. {ac}"
            for idx, ac in enumerate(story.acceptance_criteria, start=1)
        ]
    else:
        step_lines = ["_No acceptance criteria to derive steps from._"]

    # Merge Phase 1 suggestions across the three agents, dedupe while
    # preserving first-seen order, and surface them as follow-up bullets.
    suggestions: list[str] = []
    seen: set[str] = set()
    for agent_score in (phase1.quality, phase1.ambiguity, phase1.complexity):
        for suggestion in agent_score.suggestions:
            if suggestion not in seen:
                seen.add(suggestion)
                suggestions.append(suggestion)

    body_parts = ["\n".join(step_lines)]
    if suggestions:
        follow_up = "\n".join(f"- {s}" for s in suggestions)
        body_parts.append("**Follow-ups from Phase 1 review:**\n\n" + follow_up)

    return _wrap_section("Implementation Steps", "\n\n".join(body_parts))


def _render_gate_fail_block(phase1: Phase1Result) -> str:
    # Surface up to two highest-signal suggestions from Phase 1 so the
    # reader knows what to fix before re-running the pipeline.
    suggestions: list[str] = []
    seen: set[str] = set()
    for agent_score in (phase1.quality, phase1.ambiguity, phase1.complexity):
        for suggestion in agent_score.suggestions:
            if suggestion not in seen:
                seen.add(suggestion)
                suggestions.append(suggestion)
            if len(suggestions) == 2:
                break
        if len(suggestions) == 2:
            break

    # NOTE: threshold isn't stored on Phase1Result, so we describe the
    # condition with the composite score alone. The message makes the
    # causal link explicit without hard-coding a number.
    lines = [
        "_Phase 2 generation skipped: the story did not pass the Phase 1 "
        f"quality gate (composite score {phase1.composite_score:.2f})._",
        "",
        "Address the feedback below and re-run the pipeline.",
    ]
    if suggestions:
        lines.append("")
        lines.append("**Top suggestions from Phase 1:**")
        lines.append("")
        for s in suggestions:
            lines.append(f"- {s}")

    return "\n".join(lines)


def _render_definition_of_done() -> str:
    body = "\n".join(f"- [ ] {item}" for item in _DEFINITION_OF_DONE)
    return _wrap_section("Definition of Done", body)
