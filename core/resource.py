from abc import ABC, abstractmethod
from typing import Dict, Any
from transports.base import Transport

class Resource(ABC):
    def __init__(self,name: str, transport: Transport):
        self.name = name
        self.transport = transport

    @abstractmethod
    async def get_status(self) -> str:
        """Возвращает строковый статус ресурса (например: running, stopped, unknown)"""
        pass

    @abstractmethod
    async def get_metrics(self) -> Dict[str, Any]:
        """Возвращает словарь с сырыми метриками ресурса (CPU, RAM, uptime и т.д.)"""
        pass