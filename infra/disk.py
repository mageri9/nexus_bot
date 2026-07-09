# infra/disk.py
from datetime import datetime, timezone
from typing import Dict, Any
from core.resource import Resource
from core.telemetry import Metric
from transports.base import Transport


class HostDiskResource(Resource):
    """Мониторинг общего состояния и объема дискового раздела хоста"""
    def __init__(self, name: str, transport: Transport, path: str = "/host_root"):
        super().__init__(name, transport)
        self.path = path
        # У диска хоста нет управляющих действий
        self.capabilities = []

    async def get_status(self) -> str:
        try:
            await self.transport.run(["test", "-d", self.path])
            return "healthy"
        except Exception:
            return "unhealthy"

    async def get_metrics(self) -> Dict[str, Any]:
        try:
            df_output = await self.transport.run(["df", "-h", self.path])
            lines = df_output.split("\n")
            if len(lines) > 1:
                parts = [p for p in lines[1].split(" ") if p]
                if len(parts) >= 5:
                    metric = Metric(
                        key="partition_size",
                        value={
                            "size": parts[1],
                            "used": parts[2],
                            "avail": parts[3],
                            "use_percent": parts[4]
                        },
                        unit="human_readable",
                        source="df",
                        timestamp=datetime.now(timezone.utc)
                    )
                    # Использование mode="json" для сериализации datetime в строку
                    return {"partition_size": metric.model_dump(mode="json")}
            return {"error": f"Failed to parse df output: {df_output}"}
        except Exception as e:
            return {"error": str(e)}


class ProjectStorageResource(Resource):
    """Мониторинг изолированных каталогов данных конкретных проектов (ботов)"""
    def __init__(self, name: str, transport: Transport, path: str):
        super().__init__(name, transport)
        self.path = path
        # Объявляем возможность резервного копирования базы/данных папки
        self.capabilities = ["backup_db"]

    async def get_status(self) -> str:
        try:
            await self.transport.run(["test", "-d", self.path])
            return "healthy"
        except Exception:
            return "unhealthy"

    async def get_metrics(self) -> Dict[str, Any]:
        try:
            # Получаем объем папки в килобайтах (KB)
            du_output = await self.transport.run(["du", "-s", self.path])
            parts = du_output.split()
            if parts:
                size_kb = int(parts[0])
                size_bytes = size_kb * 1024 # Сохраняем в сырых байтах
                metric = Metric(
                    key="directory_size",
                    value=size_bytes,
                    unit="bytes",
                    source="du",
                    timestamp=datetime.now(timezone.utc)
                )
                # Использование mode="json" для сериализации datetime в строку
                return {"directory_size": metric.model_dump(mode="json")}
            return {"error": f"Failed to parse du output: {du_output}"}
        except Exception as e:
            return {"error": str(e)}