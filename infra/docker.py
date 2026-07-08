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
            # 1. Запрос утилизации CPU и RAM в процентах, а также физического объема
            stats_output = await self.transport.run(
                [
                    "docker",
                    "stats",
                    self.container_name,
                    "--no-stream",
                    "--format",
                    "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}",
                ]
            )

            # 2. Запрос текущего количества перезапусков контейнера
            restarts_output = await self.transport.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.State.RestartCount}}",
                    self.container_name,
                ]
            )

            restarts = 0
            try:
                restarts = int(restarts_output.strip())
            except Exception:
                pass

            if not stats_output:
                return {
                    "cpu": "0.00%",
                    "mem_perc": "0.00%",
                    "memory": "0MiB / 0MiB",
                    "restarts": restarts,
                }

            parts = stats_output.split("|")
            if len(parts) >= 3:
                return {
                    "cpu": parts[0].strip(),
                    "mem_perc": parts[1].strip(),
                    "memory": parts[2].strip(),
                    "restarts": restarts,
                }
            return {
                "cpu": "0.00%",
                "mem_perc": "0.00%",
                "memory": stats_output.strip(),
                "restarts": restarts,
            }

        except Exception as e:
            return {"error": f"Failed to fetch metrics: {str(e)}"}

    async def restart(self) -> str:
        """Перезапускает контейнер и возвращает ID контейнера"""
        return await self.transport.run(["docker", "restart", self.container_name])

    async def get_logs(self, limit: int = 10) -> str:
        """Возвращает последние N строк логов контейнера"""
        return await self.transport.run(
            ["docker", "logs", "--tail", str(limit), self.container_name]
        )