from datetime import datetime, timezone
from typing import Dict, Any
from core.resource import Resource
from core.telemetry import Metric
from transports.base import Transport


class DockerContainer(Resource):
    def __init__(self, name: str, transport: Transport, container_name: str) -> None:
        super().__init__(name, transport)
        self.container_name = container_name
        self.capabilities = ["restart", "logs"]

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
            # 1. Запрос утилизации CPU и RAM
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

            # 2. Запрос рестартов и даты старта с правильными путями в шаблоне
            inspect_output = await self.transport.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{.RestartCount}}|{{.State.StartedAt}}",
                    self.container_name,
                ]
            )

            restarts = 0
            uptime_seconds = 0
            inspect_parts = inspect_output.strip().split("|")

            if len(inspect_parts) >= 2:
                try:
                    restarts = int(inspect_parts[0])
                except ValueError:
                    pass

                try:
                    started_at_raw = inspect_parts[1]
                    # Безопасно отсекаем наносекунды для парсинга в ISO-формат
                    started_at_clean = started_at_raw.split(".")[0]
                    if started_at_clean.endswith("Z"):
                        started_at_clean = started_at_clean[:-1]

                    started_at_dt = datetime.fromisoformat(started_at_clean).replace(
                        tzinfo=timezone.utc
                    )
                    now = datetime.now(timezone.utc)

                    # Храним строго секунды (целое число), форматирование выполнит UI
                    uptime_seconds = int((now - started_at_dt).total_seconds())
                    if uptime_seconds < 0:
                        uptime_seconds = 0
                except Exception:
                    pass

            # Парсинг stats
            cpu = "0.00%"
            mem_perc = "0.00%"
            memory = "0MiB / 0MiB"

            if stats_output:
                stats_parts = stats_output.split("|")
                if len(stats_parts) >= 3:
                    cpu = stats_parts[0].strip()
                    mem_perc = stats_parts[1].strip()
                    memory = stats_parts[2].strip()

            now_ts = datetime.now(timezone.utc)

            # Сериализуем всё в json-совместимые типы через mode="json" (исключает datetime в Redis)
            return {
                "cpu": Metric(
                    key="cpu",
                    value=cpu,
                    unit="percent",
                    source="docker_stats",
                    timestamp=now_ts,
                ).model_dump(mode="json"),
                "mem_perc": Metric(
                    key="mem_perc",
                    value=mem_perc,
                    unit="percent",
                    source="docker_stats",
                    timestamp=now_ts,
                ).model_dump(mode="json"),
                "memory": Metric(
                    key="memory",
                    value=memory,
                    unit="human_readable",
                    source="docker_stats",
                    timestamp=now_ts,
                ).model_dump(mode="json"),
                "restarts": Metric(
                    key="restarts",
                    value=restarts,
                    unit="counter",
                    source="docker_inspect",
                    timestamp=now_ts,
                ).model_dump(mode="json"),
                "uptime_seconds": Metric(
                    key="uptime_seconds",
                    value=uptime_seconds,
                    unit="seconds",
                    source="docker_inspect",
                    timestamp=now_ts,
                ).model_dump(mode="json"),
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