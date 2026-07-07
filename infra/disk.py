from typing import Dict, Any
from core.resource import Resource
from transports.base import Transport


class DiskResource(Resource):
    def __init__(self, name: str, transport: Transport, path: str = "/"):
        super().__init__(name, transport)
        self.path = path

    async def get_status(self) -> str:
        try:
            # Проверяем, существует ли директория
            await self.transport.run(["test", "-d", self.path])
            return "healthy"
        except Exception:
            return "unhealthy"

    async def get_metrics(self) -> Dict[str, Any]:
        try:
            df_output = await self.transport.run(["df", "-h", self.path])
            lines = df_output.split("\n")
            if len(lines) > 1:
                # Парсим вторую строчку вывода команды df -h
                parts = [p for p in lines[1].split(" ") if p]
                if len(parts) >= 5:
                    return {
                        "size": parts[1],
                        "used": parts[2],
                        "avail": parts[3],
                        "use_percent": parts[4]
                    }
                return {"raw": df_output}
        except Exception as e:
            return {"error": str(e)}