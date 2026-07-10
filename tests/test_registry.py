import pytest
from core import AgentRegistry, ProjectAgent


def test_register_and_get_agent(registry):
    agent = ProjectAgent(name="tarot_bot", resources={})
    registry.register(agent)

    assert registry.get("tarot_bot") is agent


def test_duplicate_registration_raises_value_error(registry):
    registry.register(ProjectAgent(name="nexus", resources={}))

    with pytest.raises(ValueError):
        registry.register(ProjectAgent(name="nexus", resources={}))


def test_get_unknown_agent_raises_key_error(registry):
    with pytest.raises(KeyError):
        registry.get("does_not_exist")


def test_list_agents_returns_registered_names_in_order(registry):
    registry.register(ProjectAgent(name="a", resources={}))
    registry.register(ProjectAgent(name="b", resources={}))
    registry.register(ProjectAgent(name="c", resources={}))

    assert registry.list_agents() == ["a", "b", "c"]


def test_list_agents_empty_registry(registry):
    assert registry.list_agents() == []
