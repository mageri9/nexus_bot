from typing import Dict, Any
from core.resource import Resource
from transports.base import Transport


class DockerContainer(Resource):
    def __init__(self, name: str, transport: Transport, container_name: str) -> None:
        super().__init__(name, transport)
        self.container_name = container_name

    async def get_status(self) -> str:
        try:
            status = await self.transport.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", self.container_name]
            )
            return status.strip()
        except Exception:
            return "unknown"

    async def get_metrics(self) -> Dict[str, Any]:
        try:
            # Получаем CPU и RAM без интерактивного стрима за один вызов
            stats_output = await self.transport.run(
                ["docker", "stats", self.container_name, "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}"]
            )
            if not stats_output:
                return {"cpu": "0.00%", "memory": "0MiB / 0MiB"}

            cpu, memory = stats_output.split("|")
            return {"cpu": cpu.strip(), "memory": memory.strip()}

        except Exception as e:
            return {"error": f"Failed to fetch metrics: {str(e)}"}

    async def restart(self) -> str:
        """Перезапускает контейнер и возвращает ID контейнера"""
        return await self.transport.run(["docker", "restart", self.container_name])

    async def get_logs(self, limit: int = 10) -> str:
        """Возвращает последние N строк логов контейнера"""
        return await self.transport.run(["docker", "logs", "--tail", str(limit), self.container_name])