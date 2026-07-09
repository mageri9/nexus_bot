from abc import ABC, abstractmethod
from typing import Dict, Any, List
from transports.base import Transport

class Resource(ABC):
    def __init__(self, name: str, transport: Transport):
        self.name = name
        self.transport = transport
        # Реестр возможностей ресурса (например: ["restart", "logs", "backup_db"])
        # Заполняется конкретными классами-наследниками
        self.capabilities: List[str] = []

    @abstractmethod
    async def get_status(self) -> str:
        """Возвращает строковый статус ресурса (например: running, stopped, unknown)"""
        pass

    @abstractmethod
    async def get_metrics(self) -> Dict[str, Any]:
        """Возвращает словарь с объектами Metric в качестве значений"""
        pass