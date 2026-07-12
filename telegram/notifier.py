import html
import asyncio
from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from loguru import logger

from config import settings
from services import query_service, redis_client, ai_service, incident_service


# Контракт для Callback-данных кнопок инцидента
class IncidentActionCallback(CallbackData, prefix="inc_act"):
    id: str
    act: str  # restart, logs, ai, silence


class TelegramNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot
        # Сохраняем весь список администраторов для вещания алертов
        self.admin_ids = settings.admin_id_list if settings.admin_id_list else []

    async def on_action_success(self, event_type: str, data: dict) -> None:
        """Реагирует на успешное выполнение ручного действия"""
        logger.info(f"Notifier: Received success event: {data}")
        text = (
            f"🔔 <b>[NEXUS EVENT]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> completed successfully!"
        )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send success alert to admin {admin_id}: {e}"
                )

    async def on_action_failed(self, event_type: str, data: dict) -> None:
        """Реагирует на ошибку при выполнении ручного действия"""
        logger.error(f"Notifier: Received failure event: {data}")
        text = (
            f"⚠️ <b>[NEXUS EVENT ERROR]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> <b>failed</b>!\n"
            f"Error: <code>{data['error']}</code>"
        )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send failure alert to admin {admin_id}: {e}"
                )

    async def on_incident_opened(self, event_type: str, data: dict) -> None:
        """Форматирует и отправляет карточку открытого инцидента всем админам"""
        inc_id = data["id"]
        project = data["project"]
        resource = data["resource"]
        severity = data["severity"]
        reason = data["reason"]
        restart_count = data.get("restart_count", 0)
        logs = data.get("logs") or "No logs captured."

        # Проверка режима тишины (Silence)
        silence_key = f"nexus:silence:{project}:{resource}"
        if await redis_client.exists(silence_key):
            logger.info(
                f"Notifier: Alert for incident #{inc_id} suppressed due to active silence."
            )
            return

        # Динамическое извлечение последних CPU/RAM метрик (с поддержкой State V2)
        cpu = "N/A"
        ram = "N/A"
        try:
            cached_details = await query_service.get_agent_details(project)
            if cached_details.get("version") == 2:
                res_details = cached_details.get("containers", {}).get(resource)
                if not res_details:
                    res_details = cached_details.get("storage", {}).get(resource)

                if res_details:
                    metrics = res_details.get("metrics", {})
                    cpu_metric = metrics.get("cpu", {})
                    cpu = (
                        cpu_metric.get("value", "N/A")
                        if isinstance(cpu_metric, dict)
                        else cpu_metric
                    )
                    ram_metric = metrics.get("memory", {})
                    ram = (
                        ram_metric.get("value", "N/A")
                        if isinstance(ram_metric, dict)
                        else ram_metric
                    )
        except Exception as e:
            logger.debug(
                f"Notifier: Failed to fetch real-time metrics for incident alert: {e}"
            )

        # Экранируем логи и обрезаем под лимит
        logs_preview = "\n".join(logs.strip().split("\n")[-15:])
        if len(logs_preview) > 800:
            logs_preview = logs_preview[-800:]
        logs_preview_escaped = html.escape(logs_preview)

        text = (
            f"🚨 <b>INCIDENT #{inc_id}</b>\n\n"
            f"<b>Project:</b> <code>{project.upper()}</code>\n"
            f"<b>Resource:</b> <code>{resource}</code>\n"
            f"<b>Severity:</b> 🔴 <code>{severity}</code>\n"
            f"<b>Restart Count:</b> <code>{restart_count}</code>\n"
            f"<b>CPU:</b> <code>{cpu}</code> | <b>RAM:</b> <code>{ram}</code>\n\n"
            f"<b>Reason:</b> <code>{reason}</code>\n\n"
            f"📝 <b>Last Logs Preview:</b>\n"
            f"<pre>{logs_preview_escaped}</pre>"
        )

        builder = InlineKeyboardBuilder()
        builder.button(
            text="🔄 Restart",
            callback_data=IncidentActionCallback(id=inc_id, act="restart"),
        )
        builder.button(
            text="📝 Logs", callback_data=IncidentActionCallback(id=inc_id, act="logs")
        )
        builder.button(
            text="🧠 AI Diagnose",
            callback_data=IncidentActionCallback(id=inc_id, act="ai"),
        )
        builder.button(
            text="🔕 Silence 1h",
            callback_data=IncidentActionCallback(id=inc_id, act="silence"),
        )
        builder.adjust(2, 2)

        # Рассылаем карточки всем админам и собираем ID отправленных сообщений для ИИ-реплаев
        sent_messages = []
        for admin_id in self.admin_ids:
            try:
                sent_msg = await self.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
                sent_messages.append((admin_id, sent_msg.message_id))
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send incident alert to admin {admin_id}: {e}"
                )

        # Порождаем ровно ОДНУ фоновую задачу анализа, которая ответит реплаем всем получателям
        if sent_messages:
            asyncio.create_task(
                self._run_auto_ai_diagnose_broadcast(
                    sent_messages=sent_messages, incident_data=data
                )
            )

    async def on_incident_resolved(self, event_type: str, data: dict) -> None:
        """Форматирует и отправляет сообщение о разрешении инцидента всем админам"""
        inc_id = data["id"]
        project = data["project"]
        resource = data["resource"]
        duration = data.get("duration", 0)

        silence_key = f"nexus:silence:{project}:{resource}"
        if await redis_client.exists(silence_key):
            logger.info(
                f"Notifier: Incident #{inc_id} recovery alert skipped due to silence mode."
            )
            return

        if duration < 60:
            duration_str = f"{duration:.1f}s"
        else:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f"{minutes}m {seconds}s"

        text = (
            f"✅ <b>[RESOLVED] INCIDENT #{inc_id}</b>\n\n"
            f"<b>Project:</b> <code>{project.upper()}</code>\n"
            f"<b>Resource:</b> <code>{resource}</code>\n"
            f"<b>Status:</b> 🟢 <code>resolved</code>\n"
            f"<b>Duration of Outage:</b> <code>{duration_str}</code>"
        )

        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send incident resolution alert to admin {admin_id}: {e}"
                )

    async def _run_auto_ai_diagnose_broadcast(
        self, sent_messages: list, incident_data: dict
    ) -> None:
        """
        Неблокирующий широковещательный ИИ-анализ логов аварии.
        Делегирует сборку контекста и вызов ИИ консолидированной службе AIService.
        """
        inc_id = incident_data["id"]
        project = incident_data["project"]
        resource = incident_data["resource"]

        logger.info(
            f"Auto-AI: Starting post-incident broadcast diagnostics for Incident #{inc_id}..."
        )

        try:
            # Вызов единого метода вместо дублирования сборки промпта
            report = await ai_service.diagnose_incident(inc_id)

            # Сохраняем полученный отчет в инцидент в Redis (кэшируем результат)
            try:
                incident = await incident_service.get_incident(inc_id)
                if incident:
                    incident.ai_report = report
                    await redis_client.set(
                        f"nexus:incident:detail:{inc_id}", incident.model_dump_json()
                    )
            except Exception as cache_err:
                logger.error(
                    f"Auto-AI: Failed to cache diagnostics in Redis for #{inc_id}: {cache_err}"
                )

            ai_text = (
                f"🧠 <b>Автоматический ИИ-анализ инцидента #{inc_id}</b> ({project}:{resource}):\n\n"
                f"{html.escape(report)}"
            )

            # Рассылаем реплаи всем получателям карточки
            for chat_id, message_id in sent_messages:
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=ai_text,
                        parse_mode="HTML",
                        reply_to_message_id=message_id,
                    )
                except Exception as e:
                    logger.error(f"Auto-AI: Failed to reply to admin {chat_id}: {e}")

            logger.info(
                f"Auto-AI: Post-incident analysis successfully broadcasted for #{inc_id}"
            )

        except Exception as e:
            logger.error(
                f"Auto-AI: Broadcast diagnostics failed for incident #{inc_id}: {e}"
            )
            for chat_id, message_id in sent_messages:
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ <i>Не удалось сформировать автоматический ИИ-анализ для инцидента #{inc_id}: сервис недоступен или превышена квота запросов.</i>",
                        parse_mode="HTML",
                        reply_to_message_id=message_id,
                    )
                except Exception:
                    pass

    async def on_devops_workflow_success(self, event_type: str, data: dict) -> None:
        """Оповещает всех админов об успешном деплое / прохождении пайплайна"""
        text = (
            f"🚀 <b>CI/CD: Build & Deployment Success</b>\n\n"
            f"<b>Repository:</b> <code>{data.get('repository')}</code>\n"
            f"<b>Workflow:</b> <code>{data.get('workflow_name')}</code>\n"
            f"<b>Branch:</b> <code>{data.get('branch')}</code>\n"
            f"<b>Commit:</b> <code>{data.get('commit_sha')}</code>\n"
            f"<b>Author:</b> @{data.get('author')}\n"
            f"<b>Message:</b> <code>{data.get('commit_message')}</code>\n\n"
            f"🔗 <a href='{data.get('url')}'>View Workflow Logs</a>"
        )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send success workflow alert to admin {admin_id}: {e}"
                )

    async def on_devops_workflow_failure(self, event_type: str, data: dict) -> None:
        """Оповещает всех админов о падении CI/CD пайплайна"""
        text = (
            f"❌ <b>CI/CD: Build Failed</b>\n\n"
            f"<b>Repository:</b> <code>{data.get('repository')}</code>\n"
            f"<b>Workflow:</b> <code>{data.get('workflow_name')}</code>\n"
            f"<b>Branch:</b> <code>{data.get('branch')}</code>\n"
            f"<b>Commit:</b> <code>{data.get('commit_sha')}</code>\n"
            f"<b>Author:</b> @{data.get('author')}\n"
            f"<b>Message:</b> <code>{data.get('commit_message')}</code>\n\n"
            f"⚠️ <i>Сборка завершилась аварийно! Требуется ручная проверка кода.</i>\n\n"
            f"🔗 <a href='{data.get('url')}'>Inspect Failure Details</a>"
        )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"Notifier: Failed to send failure workflow alert to admin {admin_id}: {e}")