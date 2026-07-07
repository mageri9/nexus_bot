from abc import ABC, abstractmethod


class Transport(ABC):
    @abstractmethod
    async def run(self, cmd: list[str]) -> str:
        """
        Выполняет команду и возвращает её stdout в виде строки.
        В случае ошибки выполнения должна вызывать RuntimeError.
        """
        pass