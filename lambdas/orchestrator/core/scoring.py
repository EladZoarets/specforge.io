from __future__ import annotations

from .models import AgentScore, Phase1Result

_WEIGHT_QUALITY = 0.40
_WEIGHT_AMBIGUITY = 0.35
_WEIGHT_COMPLEXITY = 0.25


def compute_composite(quality: float, ambiguity: float, complexity: float) -> float:
    return round(
        quality * _WEIGHT_QUALITY
        + ambiguity * _WEIGHT_AMBIGUITY
        + complexity * _WEIGHT_COMPLEXITY,
        2,
    )


def evaluate_gate(composite: float, threshold: float) -> bool:
    return composite >= threshold


def build_phase1_result(
    quality: AgentScore,
    ambiguity: AgentScore,
    complexity: AgentScore,
    threshold: float,
) -> Phase1Result:
    composite = compute_composite(quality.score, ambiguity.score, complexity.score)
    return Phase1Result(
        quality=quality,
        ambiguity=ambiguity,
        complexity=complexity,
        composite_score=composite,
        passed_gate=evaluate_gate(composite, threshold),
    )
