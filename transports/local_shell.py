import asyncio
from loguru import logger
from transports.base import Transport


class LocalShellTransport(Transport):
    async def run(self, cmd: list[str]) -> str:
        logger.debug(f"LocalShell executing: {' '.join(cmd)}")

        # Запускаем команду асинхронно
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        # Если код возврата не 0 — это ошибка выполнения
        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Command failed (code {proc.returncode}): {error_msg}")
            raise RuntimeError(error_msg or f"Command failed with exit code {proc.returncode}")

        return stdout.decode().strip()