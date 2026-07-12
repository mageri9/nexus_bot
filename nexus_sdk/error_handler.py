import hmac
import hashlib
import json
import traceback
import httpx
import asyncio
from datetime import datetime, timezone
from aiogram import Dispatcher
from aiogram.types import ErrorEvent


class NexusSDK:
    def __init__(self, endpoint_url: str, app_secret: str, project_name: str):
        self.endpoint_url = endpoint_url
        self.app_secret = app_secret
        self.project_name = project_name
        self._client = httpx.AsyncClient(timeout=5.0)
        self._heartbeat_task: asyncio.Task | None = None

    async def close(self) -> None:
        """Закрывает внутренний HTTP-клиент и останавливает фоновые задачи"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()


    def sign_payload(self, body_bytes: bytes) -> str:
        """Генерирует HMAC-SHA256 подпись тела запроса"""
        digest = hmac.new(
            self.app_secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()
        return f"sha256={digest}"

    async def report_error(self, exception: Exception, context: str = "") -> None:
        """Формирует и отправляет структурированный отчет об ошибке в Nexus"""
        tb_str = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )

        payload = {
            "project": self.project_name,
            "exception_type": type(exception).__name__,
            "message": str(exception),
            "traceback": tb_str,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        body_bytes = json.dumps(payload).encode("utf-8")
        signature = self.sign_payload(body_bytes)

        headers = {
            "Content-Type": "application/json",
            "X-Nexus-Signature-256": signature,
        }

        try:
            resp = await self._client.post(
                self.endpoint_url, content=body_bytes, headers=headers
            )
            if resp.status_code != 200:
                print(f"[NexusSDK] Failed to send error report: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"[NexusSDK] Connection error to Nexus: {e}")

    def start_heartbeat(self, interval_seconds: int = 15) -> asyncio.Task:
        """Запускает фоновую задачу периодической отправки Heartbeat в Nexus"""
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(interval_seconds)
        )
        return self._heartbeat_task

    async def _heartbeat_loop(self, interval: int) -> None:
        while True:
            try:
                payload = {
                    "project": self.project_name,
                    "event_type": "app:heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                body_bytes = json.dumps(payload).encode("utf-8")
                signature = self.sign_payload(body_bytes)

                headers = {
                    "Content-Type": "application/json",
                    "X-Nexus-Signature-256": signature,
                }
                resp = await self._client.post(
                    self.endpoint_url, content=body_bytes, headers=headers
                )
                if resp.status_code != 200:
                    print(
                        f"[NexusSDK] Heartbeat failed: {resp.status_code} {resp.text}"
                    )
            except Exception as e:
                print(f"[NexusSDK] Heartbeat connection error: {e}")

            await asyncio.sleep(interval)

    def register_aiogram_error_handler(self, dp: Dispatcher) -> None:
        """Интегрирует глобальный перехватчик исключений в aiogram Dispatcher"""
        @dp.errors()
        async def aiogram_error_handler(event: ErrorEvent):
            exception = event.exception
            # Попытка сериализовать апдейт aiogram для контекста
            update_ctx = (
                str(event.update.model_dump())
                if hasattr(event.update, "model_dump")
                else str(event.update)
            )
            await self.report_error(exception, context=update_ctx)
            # Пробрасываем ошибку дальше для штатной работы бота
            raise exception

    def register_ptb_error_handler(self, app) -> None:
        """Интегрирует глобальный перехватчик исключений в python-telegram-bot Application"""

        async def ptb_error_handler(update: object, context) -> None:
            exception = context.error
            # Захват контекста обновления в строковом виде
            update_ctx = (
                str(update.to_dict()) if hasattr(update, "to_dict") else str(update)
            )
            await self.report_error(exception, context=update_ctx)
            # Пробрасываем исключение дальше для штатной работы фреймворка
            raise exception

        app.add_error_handler(ptb_error_handler)