"""
Unit tests for ``pipeline.writer.assemble_spec``.

These are pure-function tests — no I/O, no async, no mocking. They pin the
section order, section contents, and gate-fail branching that downstream
components (S3 upload, PR body rendering) rely on.
"""

from __future__ import annotations

from core.models import AgentScore, JiraStory, Phase1Result, Phase2Result
from pipeline.writer import assemble_spec

_SECTION_HEADINGS: tuple[str, ...] = (
    "## Story",
    "## Evaluation Summary",
    "## Architecture",
    "## API Design",
    "## Implementation Steps",
    "## Edge Cases",
    "## Testing Strategy",
    "## Definition of Done",
)


def _score(name: str, value: float, suggestions: list[str] | None = None) -> AgentScore:
    return AgentScore(
        agent_name=name,
        score=value,
        rationale=f"{name} rationale",
        suggestions=suggestions or [],
    )


def _story(story_points: int | None = 3) -> JiraStory:
    return JiraStory(
        id="SPEC-42",
        title="Build a webhook receiver",
        description="As a user I want webhooks to be validated.",
        acceptance_criteria=[
            "Given a valid signature, When POST /webhook, Then return 200",
            "Given an invalid signature, When POST /webhook, Then return 401",
        ],
        story_points=story_points,
    )


def _phase1_pass() -> Phase1Result:
    return Phase1Result(
        quality=_score("quality", 8.0, ["Tighten AC wording"]),
        ambiguity=_score("ambiguity", 7.5, ["Clarify auth path"]),
        complexity=_score("complexity", 6.5, ["Split into two tasks"]),
        composite_score=7.45,
        passed_gate=True,
    )


def _phase1_fail() -> Phase1Result:
    return Phase1Result(
        quality=_score("quality", 4.0, ["Rewrite the user story"]),
        ambiguity=_score("ambiguity", 3.5, ["Specify the auth scheme"]),
        complexity=_score("complexity", 4.0, ["Break into smaller stories"]),
        composite_score=3.85,
        passed_gate=False,
    )


def _phase2() -> Phase2Result:
    return Phase2Result(
        architecture="Architecture body text.",
        api_design="API design body text.",
        edge_cases="Edge cases body text.",
        testing_strategy="Testing strategy body text.",
    )


def test_all_eight_section_headers_present_on_happy_path():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    for heading in _SECTION_HEADINGS:
        assert heading in output, f"missing section: {heading}"


def test_all_eight_section_headers_present_on_gate_fail():
    # Gate-fail must still emit every section header — they just render
    # the skip notice instead of agent-generated body.
    output = assemble_spec(_story(), _phase1_fail(), None)
    for heading in _SECTION_HEADINGS:
        assert heading in output, f"missing section: {heading}"


def test_section_order_matches_spec():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    indices = [output.index(heading) for heading in _SECTION_HEADINGS]
    assert indices == sorted(indices), (
        f"sections are not in the expected order; got indices {indices}"
    )


def test_section_order_on_gate_fail():
    output = assemble_spec(_story(), _phase1_fail(), None)
    indices = [output.index(heading) for heading in _SECTION_HEADINGS]
    assert indices == sorted(indices)


def test_composite_score_appears_in_evaluation_summary_with_two_decimals():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    assert "**Composite Score:** 7.45" in output


def test_passed_gate_true_rendered():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    assert "**Passed Gate:** True" in output


def test_passed_gate_false_rendered():
    output = assemble_spec(_story(), _phase1_fail(), None)
    assert "**Passed Gate:** False" in output


def test_gate_fail_block_appears_in_sections_3_through_7():
    output = assemble_spec(_story(), _phase1_fail(), None)
    # The skip notice should appear in each of the five Phase-2-driven
    # sections. Locate each section's span and assert "skipped" shows up.
    gated_sections = (
        "## Architecture",
        "## API Design",
        "## Implementation Steps",
        "## Edge Cases",
        "## Testing Strategy",
    )
    # Build a list of (heading, start, end) spans.
    spans = []
    for heading in gated_sections:
        start = output.index(heading)
        # next heading after this one
        next_heading_starts = [
            output.index(h) for h in _SECTION_HEADINGS if output.index(h) > start
        ]
        end = min(next_heading_starts) if next_heading_starts else len(output)
        spans.append((heading, start, end))

    for heading, start, end in spans:
        body = output[start:end]
        assert "skipped" in body, f"{heading} section missing gate-fail notice"


def test_gate_fail_block_includes_composite_score_and_a_phase1_suggestion():
    phase1 = _phase1_fail()
    output = assemble_spec(_story(), phase1, None)
    # Composite score is referenced in the skip block.
    assert f"{phase1.composite_score:.2f}" in output
    # At least one suggestion from Phase 1 appears.
    assert "Rewrite the user story" in output


def test_definition_of_done_always_present_on_pass():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    assert "## Definition of Done" in output
    assert "- [ ] Unit tests pass" in output
    assert "- [ ] Acceptance criteria verified by PM" in output


def test_definition_of_done_always_present_on_gate_fail():
    output = assemble_spec(_story(), _phase1_fail(), None)
    assert "## Definition of Done" in output
    assert "- [ ] Unit tests pass" in output


def test_deterministic_same_inputs_produce_identical_output():
    story = _story()
    phase1 = _phase1_pass()
    phase2 = _phase2()
    a = assemble_spec(story, phase1, phase2)
    b = assemble_spec(story, phase1, phase2)
    assert a == b


def test_deterministic_gate_fail_same_inputs_produce_identical_output():
    story = _story()
    phase1 = _phase1_fail()
    a = assemble_spec(story, phase1, None)
    b = assemble_spec(story, phase1, None)
    assert a == b


def test_story_section_includes_id_title_and_all_acceptance_criteria():
    story = _story()
    output = assemble_spec(story, _phase1_pass(), _phase2())
    story_section = output[output.index("## Story") : output.index("## Evaluation Summary")]
    assert "SPEC-42" in story_section
    assert "Build a webhook receiver" in story_section
    for ac in story.acceptance_criteria:
        assert ac in story_section


def test_story_section_renders_unspecified_when_story_points_missing():
    story = _story(story_points=None)
    output = assemble_spec(story, _phase1_pass(), _phase2())
    story_section = output[output.index("## Story") : output.index("## Evaluation Summary")]
    assert "unspecified" in story_section


def test_story_section_renders_integer_story_points():
    story = _story(story_points=5)
    output = assemble_spec(story, _phase1_pass(), _phase2())
    story_section = output[output.index("## Story") : output.index("## Evaluation Summary")]
    assert "**Story Points:** 5" in story_section


def test_implementation_steps_length_matches_acceptance_criteria_on_pass():
    story = _story()
    output = assemble_spec(story, _phase1_pass(), _phase2())
    # Find the Implementation Steps section body.
    start = output.index("## Implementation Steps")
    end = output.index("## Edge Cases")
    section = output[start:end]
    # Count numbered list entries of the form ``N. ``. We specifically
    # look for lines starting with ``{idx}. `` where idx is 1..N.
    for idx, ac in enumerate(story.acceptance_criteria, start=1):
        assert f"{idx}. {ac}" in section
    # And make sure there's no (N+1) step.
    next_idx = len(story.acceptance_criteria) + 1
    assert f"\n{next_idx}. " not in section


def test_implementation_steps_surfaces_phase1_suggestions_deduped():
    # Duplicated suggestion across agents should appear only once.
    phase1 = Phase1Result(
        quality=_score("quality", 8.0, ["Add retry logic"]),
        ambiguity=_score("ambiguity", 7.0, ["Add retry logic", "Document auth"]),
        complexity=_score("complexity", 7.0, []),
        composite_score=7.45,
        passed_gate=True,
    )
    output = assemble_spec(_story(), phase1, _phase2())
    start = output.index("## Implementation Steps")
    end = output.index("## Edge Cases")
    section = output[start:end]
    # "Add retry logic" appears exactly once as a bullet.
    assert section.count("- Add retry logic") == 1
    assert "- Document auth" in section


def test_evaluation_summary_table_has_three_agent_rows():
    output = assemble_spec(_story(), _phase1_pass(), _phase2())
    start = output.index("## Evaluation Summary")
    end = output.index("## Architecture")
    section = output[start:end]
    assert "| Quality |" in section
    assert "| Ambiguity |" in section
    assert "| Complexity |" in section
