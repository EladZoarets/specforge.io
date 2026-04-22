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

import re

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

# Line-ish separators we normalize to spaces inside table cells so rationale
# text never breaks out of its row: CR/LF in any combination plus the two
# Unicode line/paragraph separators that some LLM outputs emit.
_TABLE_CELL_LINE_BREAKS = re.compile(r"\r\n|\r|\n|\u2028|\u2029")
_WHITESPACE_RUN = re.compile(r"\s+")


def _escape_markdown_structural(text: str) -> str:
    """Neutralize characters that can break top-level document structure.

    - Leading ``#`` on any line is prefixed with ``\\`` so story-supplied
      text can't inject new headings that shift the 8-section layout.
    - Leading ``|`` on any line is prefixed with ``\\`` so story-supplied
      text can't be mistaken for a Markdown table row.

    Inline Markdown (bold, italic, inline code, mid-line ``#``/``|``) is
    left untouched — stories should still render with reasonable formatting.
    """
    if not text:
        return text
    lines = text.split("\n")
    escaped: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("|"):
            # Preserve the original leading whitespace, then escape the
            # first structural character.
            indent_len = len(line) - len(stripped)
            escaped.append(line[:indent_len] + "\\" + stripped)
        else:
            escaped.append(line)
    return "\n".join(escaped)


def _strip_duplicate_heading(body: str, heading: str) -> str:
    """Remove a leading ``## heading`` or ``# heading`` line from ``body``.

    Phase 2 agents are prompted not to emit their own section heading, but
    some models drift and prepend one anyway. When that happens, the writer
    would stack two identical headings. Strip the duplicate (case-insensitive
    match, trailing whitespace tolerated) and any blank lines that follow
    before the real body content.
    """
    if not body:
        return body
    # Only strip if the leading non-whitespace line is the duplicate.
    leading_ws_len = len(body) - len(body.lstrip("\n"))
    rest = body[leading_ws_len:]
    first_line, sep, remainder = rest.partition("\n")
    normalized = first_line.strip().lower()
    candidates = (
        f"## {heading}".lower(),
        f"# {heading}".lower(),
    )
    if normalized not in candidates:
        return body
    # Drop the heading line plus any immediately-following blank lines.
    remainder = remainder.lstrip("\n") if sep else ""
    return remainder


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
        # Strip duplicate section headings the Phase 2 agents may have
        # emitted despite the prompt telling them not to — otherwise the
        # writer stacks ``## Architecture`` twice, etc.
        sections.extend(
            [
                _wrap_section(
                    "Architecture",
                    _strip_duplicate_heading(phase2.architecture, "Architecture"),
                ),
                _wrap_section(
                    "API Design",
                    _strip_duplicate_heading(phase2.api_design, "API Design"),
                ),
                _render_implementation_steps(story, phase1),
                _wrap_section(
                    "Edge Cases",
                    _strip_duplicate_heading(phase2.edge_cases, "Edge Cases"),
                ),
                _wrap_section(
                    "Testing Strategy",
                    _strip_duplicate_heading(
                        phase2.testing_strategy, "Testing Strategy"
                    ),
                ),
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

    # Neutralize story-derived text so an injected ``# HACKED`` or leading
    # ``|`` can't break the 8-section layout / table rendering.
    safe_id = _escape_markdown_structural(story.id)
    safe_title = _escape_markdown_structural(story.title)
    safe_description = _escape_markdown_structural(story.description)

    if story.acceptance_criteria:
        ac_lines = "\n".join(
            f"{idx}. {_escape_markdown_structural(ac)}"
            for idx, ac in enumerate(story.acceptance_criteria, start=1)
        )
    else:
        ac_lines = "_No acceptance criteria provided._"

    body = (
        f"**ID:** {safe_id}\n\n"
        f"**Title:** {safe_title}\n\n"
        f"**Description:**\n\n{safe_description}\n\n"
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
        # Rationale is free-text from an LLM. Normalize every line-ish
        # separator (LF, CR, CRLF, U+2028, U+2029) to a single space so the
        # cell stays on one logical row, collapse whitespace runs, then
        # strip edges and apply the structural escape (for stray leading
        # ``#``/``|`` that survive normalization) plus the table-cell pipe
        # escape.
        rationale = _TABLE_CELL_LINE_BREAKS.sub(" ", agent_score.rationale)
        rationale = _WHITESPACE_RUN.sub(" ", rationale).strip()
        rationale = _escape_markdown_structural(rationale)
        rationale = rationale.replace("|", "\\|")
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
            f"{idx}. {_escape_markdown_structural(ac)}"
            for idx, ac in enumerate(story.acceptance_criteria, start=1)
        ]
    else:
        step_lines = ["_No acceptance criteria to derive steps from._"]

    # Merge Phase 1 suggestions across the three agents, dedupe while
    # preserving first-seen order, and surface them as follow-up bullets.
    # Dedupe on the raw suggestion (so upstream duplicates are collapsed
    # regardless of structural escape), then escape before rendering.
    suggestions: list[str] = []
    seen: set[str] = set()
    for agent_score in (phase1.quality, phase1.ambiguity, phase1.complexity):
        for suggestion in agent_score.suggestions:
            if suggestion not in seen:
                seen.add(suggestion)
                suggestions.append(suggestion)

    body_parts = ["\n".join(step_lines)]
    if suggestions:
        follow_up = "\n".join(
            f"- {_escape_markdown_structural(s)}" for s in suggestions
        )
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
            lines.append(f"- {_escape_markdown_structural(s)}")

    return "\n".join(lines)


def _render_definition_of_done() -> str:
    body = "\n".join(f"- [ ] {item}" for item in _DEFINITION_OF_DONE)
    return _wrap_section("Definition of Done", body)
