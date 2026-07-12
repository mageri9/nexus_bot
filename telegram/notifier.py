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

        # Рассылаем карточки всем админам (без автоматического запуска фонового ИИ-анализа)
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send incident alert to admin {admin_id}: {e}"
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
                logger.error(
                    f"Notifier: Failed to send failure workflow alert to admin {admin_id}: {e}"
                )

    async def on_anomaly_detected(self, event_type: str, data: dict) -> None:
        """
        Оповещает администраторов об обнаружении нетипичного поведения ресурсов.
        Учитывает активный режим тишины для подавления дублирующих уведомлений.
        """
        project = data.get("project", "unknown")
        resource = data.get("resource", "unknown")
        metric = data.get("metric", "unknown")
        current_value = data.get("current_value", "N/A")
        mean = data.get("mean", "N/A")
        std = data.get("std", "N/A")

        # 1. Проверяем режим тишины в Redis, чтобы не спамить
        silence_key = f"nexus:silence:{project}:{resource}"
        if await redis_client.exists(silence_key):
            logger.info(
                f"Notifier: Anomaly alert for {project}:{resource} suppressed due to active silence."
            )
            return

        # 2. Форматируем сообщение
        metric_display = (
            "Процессор (CPU)" if metric == "cpu" else "Оперативная память (RAM)"
        )
        text = (
            f"⚠️ <b>[АНМАЛИЯ] Нетипичное поведение ресурса!</b>\n\n"
            f"<b>Проект:</b> <code>{project.upper()}</code>\n"
            f"<b>Ресурс:</b> <code>{resource}</code>\n"
            f"<b>Показатель:</b> {metric_display}\n\n"
            f"<b>Текущее значение:</b> 🔴 <code>{current_value}</code>\n"
            f"<b>Ожидаемое (среднее):</b> <code>{mean}</code> (нормальное отклонение: <code>{std}</code>)\n\n"
            f"<i>Рекомендуется проверить логи и статус контейнера.</i>"
        )

        # 3. Отправляем сообщение всем зарегистрированным администраторам
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(
                    f"Notifier: Failed to send anomaly alert to admin {admin_id}: {e}"
                )


    async def on_incident_risk_detected(self, event_type: str, data: dict) -> None:
        """
        Оповещает администраторов о предиктивно обнаруженном риске инцидента.
        Использует режим тишины для исключения лишнего спама.
        """
        project = data.get("project", "unknown")
        resource = data.get("resource", "unknown")
        ml_risk = data.get("ml_risk", 0.0)
        health_score = data.get("health_score", 100)

        # 1. Проверяем режим тишины
        silence_key = f"nexus:silence:{project}:{resource}"
        if await redis_client.exists(silence_key):
            logger.info(
                f"Notifier: Predictive risk alert for {project}:{resource} suppressed due to silence."
            )
            return

        # 2. Форматируем сообщение
        text = (
            f"🔮 <b>[ПРОГНОЗ] Высокая угроза инцидента!</b>\n\n"
            f"<b>Проект:</b> <code>{project.upper()}</code>\n"
            f"<b>Ресурс:</b> <code>{resource}</code>\n\n"
            f"<b>Вероятность аварии (ML):</b> 🔴 <code>{ml_risk * 100:.1f}%</code>\n"
            f"<b>Текущее здоровье (Правила):</b> 🟢 <code>{health_score}%</code>\n\n"
            f"<i>ИИ прогнозирует критический сбой в течение ближайших 30 минут, хотя правила не фиксируют активных проблем. Рекомендуется ручная проверка.</i>"
        )

        # 3. Рассылка администраторам
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Notifier: Failed to send predictive risk alert to admin {admin_id}: {e}")