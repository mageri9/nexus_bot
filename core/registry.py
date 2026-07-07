from typing import Dict, List
from core.agent import ProjectAgent


class AgentRegistry:
    def __init__(self):
        self._agents: Dict[str, ProjectAgent] = {}

    def register(self, agent: ProjectAgent) -> None:
        """Регистрирует проект в реестре. Вызывает ошибку при дублировании имени."""
        if agent.name in self._agents:
            raise ValueError(f"Agent with name '{agent.name}' is already registered.")
        self._agents[agent.name] = agent

    def get(self, name: str) -> ProjectAgent:
        """Возвращает агента по имени. Вызывает ошибку, если проект не зарегистрирован."""
        agent = self._agents.get(name)
        if not agent:
            raise KeyError(f"Agent '{name}' is not registered.")
        return agent

    def list_agents(self) -> List[str]:
        """Возвращает список имен всех зарегистрированных проектов."""
        return list(self._agents.keys())