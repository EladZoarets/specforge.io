import pytest

from core.models import AgentScore
from core.scoring import (
    _WEIGHT_AMBIGUITY,
    _WEIGHT_COMPLEXITY,
    _WEIGHT_QUALITY,
    build_phase1_result,
    compute_composite,
    evaluate_gate,
)


def _make_score(name: str, value: float) -> AgentScore:
    return AgentScore(agent_name=name, score=value, rationale="r", suggestions=[])


def test_weights_sum_to_one():
    assert _WEIGHT_QUALITY + _WEIGHT_AMBIGUITY + _WEIGHT_COMPLEXITY == pytest.approx(1.0)


def test_compute_composite_known_input():
    # 8*0.40 + 7*0.35 + 6*0.25 = 3.20 + 2.45 + 1.50 = 7.15
    assert compute_composite(8.0, 7.0, 6.0) == pytest.approx(7.15)


def test_compute_composite_rounds_to_2dp():
    # 7*0.40 + 6*0.35 + 5*0.25 = 2.80 + 2.10 + 1.25 = 6.15
    result = compute_composite(7.0, 6.0, 5.0)
    assert result == round(result, 2)


def test_evaluate_gate_passes_at_threshold():
    assert evaluate_gate(7.0, 7.0) is True


def test_evaluate_gate_passes_above_threshold():
    assert evaluate_gate(7.5, 7.0) is True


def test_evaluate_gate_fails_below_threshold():
    assert evaluate_gate(6.99, 7.0) is False


def test_build_phase1_result_gate_pass():
    q = _make_score("quality", 8.0)
    a = _make_score("ambiguity", 8.0)
    c = _make_score("complexity", 8.0)
    result = build_phase1_result(q, a, c, threshold=7.0)
    assert result.composite_score == pytest.approx(8.0)
    assert result.passed_gate is True
    assert result.quality is q
    assert result.ambiguity is a
    assert result.complexity is c


def test_build_phase1_result_gate_fail():
    q = _make_score("quality", 5.0)
    a = _make_score("ambiguity", 5.0)
    c = _make_score("complexity", 5.0)
    result = build_phase1_result(q, a, c, threshold=7.0)
    assert result.composite_score == pytest.approx(5.0)
    assert result.passed_gate is False
