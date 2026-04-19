import pytest
from agents.registry import AgentRegistry


def test_register_and_get_roundtrip():
    registry = AgentRegistry()
    sentinel = object()
    registry.register("quality", sentinel)
    assert registry.get("quality") is sentinel


def test_get_missing_raises_key_error():
    registry = AgentRegistry()
    with pytest.raises(KeyError):
        registry.get("quality")


def test_register_overwrites_existing():
    registry = AgentRegistry()
    first = object()
    second = object()
    registry.register("quality", first)
    registry.register("quality", second)
    assert registry.get("quality") is second


def test_names_returns_registered_keys():
    registry = AgentRegistry()
    registry.register("a", object())
    registry.register("b", object())
    assert sorted(registry.names()) == ["a", "b"]


def test_contains_reflects_registration():
    registry = AgentRegistry()
    assert "quality" not in registry
    registry.register("quality", object())
    assert "quality" in registry
