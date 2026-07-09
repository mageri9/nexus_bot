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
        # Берем первого админа из списка для отправки системных алертов
        self.admin_id = settings.admin_id_list[0] if settings.admin_id_list else 0

    async def on_action_success(self, event_type: str, data: dict) -> None:
        """Реагирует на успешное выполнение ручного действия"""
        logger.info(f"Notifier: Received success event: {data}")
        text = (
            f"🔔 <b>[NEXUS EVENT]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> completed successfully!"
        )
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Notifier failed to send success message: {e}")

    async def on_action_failed(self, event_type: str, data: dict) -> None:
        """Реагирует на ошибку при выполнении ручного действия"""
        logger.error(f"Notifier: Received failure event: {data}")
        text = (
            f"⚠️ <b>[NEXUS EVENT ERROR]</b>\n"
            f"Action <code>{data['action']}</code> on "
            f"<code>{data['agent']}:{data['resource']}</code> <b>failed</b>!\n"
            f"Error: <code>{data['error']}</code>"
        )
        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Notifier failed to send failure message: {e}")

    async def on_incident_opened(self, event_type: str, data: dict) -> None:
        """Форматирует и отправляет карточку открытого инцидента"""
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
                f"Notifier: Alert for incident #{inc_id} suppressed due to active 1h silence."
            )
            return

        # Динамическое извлечение последних CPU/RAM метрик из кэша коллектора (с поддержкой State V2)
        cpu = "N/A"
        ram = "N/A"
        try:
            cached_details = await query_service.get_agent_details(project)
            if cached_details.get("version") == 2:
                # Ищем ресурс в контейнерах, а если его там нет — во внешних хранилищах
                res_details = cached_details.get("containers", {}).get(resource)
                if not res_details:
                    res_details = cached_details.get("storage", {}).get(resource)

                if res_details:
                    metrics = res_details.get("metrics", {})

                    # Извлекаем данные из структуры Metric V2 (значение лежит во вложенном ключе "value")
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

        # Обрезка логов под лимиты Telegram-сообщений
        logs_preview = "\n".join(logs.strip().split("\n")[-15:])
        if len(logs_preview) > 800:
            logs_preview = logs_preview[-800:]

        text = (
            f"🚨 <b>INCIDENT #{inc_id}</b>\n\n"
            f"<b>Project:</b> <code>{project.upper()}</code>\n"
            f"<b>Resource:</b> <code>{resource}</code>\n"
            f"<b>Severity:</b> 🔴 <code>{severity}</code>\n"
            f"<b>Restart Count:</b> <code>{restart_count}</code>\n"
            f"<b>CPU:</b> <code>{cpu}</code> | <b>RAM:</b> <code>{ram}</code>\n\n"
            f"<b>Reason:</b> <code>{reason}</code>\n\n"
            f"📝 <b>Last Logs Preview:</b>\n"
            f"<pre>{logs_preview}</pre>"
        )

        # Клавиатура управления инцидентом (Кнопка AI Diagnose сохраняется для повторных ручных вызовов)
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Restart", callback_data=IncidentActionCallback(id=inc_id, act="restart"))
        builder.button(text="📝 Logs", callback_data=IncidentActionCallback(id=inc_id, act="logs"))
        builder.button(text="🧠 AI Diagnose", callback_data=IncidentActionCallback(id=inc_id, act="ai"))
        builder.button(text="🔕 Silence 1h", callback_data=IncidentActionCallback(id=inc_id, act="silence"))
        builder.adjust(2, 2)

        try:
            sent_msg = await self.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )

            # Асинхронно порождаем фоновую задачу ИИ-диагностики логов
            asyncio.create_task(
                self._run_auto_ai_diagnose(
                    chat_id=self.admin_id,
                    message_id=sent_msg.message_id,
                    incident_data=data
                )
            )

        except Exception as e:
            logger.error(f"Notifier: Failed to send incident opened message: {e}")

    async def on_incident_resolved(self, event_type: str, data: dict) -> None:
        """Форматирует и отправляет сообщение о разрешении инцидента"""
        inc_id = data["id"]
        project = data["project"]
        resource = data["resource"]
        duration = data.get("duration", 0)

        # Проверяем режим тишины
        silence_key = f"nexus:silence:{project}:{resource}"
        if await redis_client.exists(silence_key):
            logger.info(f"Notifier: Incident #{inc_id} recovery alert skipped due to silence mode.")
            return

        # Форматирование длительности простоя
        if duration < 60:
            duration_str = f"{duration:.1f}s"
        else:
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            duration_str = f"{minutes}m {seconds}s"

        text = (
            f"✅ <b>INCIDENT RESOLVED #{inc_id}</b>\n\n"
            f"<b>Project:</b> <code>{project.upper()}</code>\n"
            f"<b>Resource:</b> <code>{resource}</code>\n"
            f"<b>Status:</b> 🟢 <code>resolved</code>\n"
            f"<b>Duration of Outage:</b> <code>{duration_str}</code>"
        )

        try:
            await self.bot.send_message(chat_id=self.admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Notifier: Failed to send incident resolved message: {e}")

    async def _run_auto_ai_diagnose(self, chat_id: int, message_id: int, incident_data: dict) -> None:
        """
        Неблокирующая фоновая задача автоматической ИИ-диагностики логов аварии.
        Высылает аналитический отчет ответом (Reply) на исходное сообщение инцидента.
        """
        inc_id = incident_data["id"]
        project = incident_data["project"]
        resource = incident_data["resource"]
        restart_count = incident_data.get("restart_count", 0)
        logs = incident_data.get("logs") or "Логи отсутствуют."

        logger.info(f"Auto-AI: Starting background diagnostics for Incident #{inc_id}...")

        try:
            # 1. Формируем подробный DevOps контекст для модели Gemma 4
            system_prompt = (
                "Вы — Nexus AI, опытный системный администратор и DevOps-эксперт.\n"
                "Ниже представлены логи и контекст аварии в инфраструктуре.\n"
                "Ваша задача: провести автоматическую экспресс-диагностику и дать ответ на русском языке.\n\n"
                f"Инцидент: #{inc_id}\n"
                f"Проект: {project.upper()}\n"
                f"Сбойный сервис: {resource}\n"
                f"Количество рестартов: {restart_count}\n\n"
                f"Логи сбоя:\n{logs}\n\n"
                "Сформулируйте профессиональный, предельно емкий отчет:\n"
                "1. Суть ошибки (1-2 емкие фразы, почему упал).\n"
                "2. Возможная техническая причина.\n"
                "3. Рекомендуемые шаги по устранению (2-3 конкретных действия)."
            )

            # 2. Вызов ИИ-модели через клиент aitunnel
            response = await ai_service.client.chat.completions.create(
                model=settings.AITUNNEL_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Проанализируй аварию."},
                ],
                temperature=0.3,
                max_tokens=600,
            )

            report = response.choices[0].message.content

            # Публикуем событие расхода токенов для автоматической диагностики инцидента
            usage = response.usage
            if usage:
                from services import event_bus

                await event_bus.publish(
                    "ai.request",
                    {
                        "project": "nexus_incident",
                        "provider": "aitunnel",
                        "model": settings.AITUNNEL_MODEL,
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                    },
                )

            # 3. Кэшируем полученный отчет в инцидент в Redis
            try:
                incident = await incident_service.get_incident(inc_id)
                if incident:
                    incident.ai_report = report
                    await redis_client.set(f"nexus:incident:detail:{inc_id}", incident.model_dump_json())
            except Exception as cache_err:
                logger.error(
                    f"Auto-AI: Failed to cache diagnostics in Redis for #{inc_id}: {cache_err}"
                )

            # 4. Отправляем отчет в виде Reply к сообщению инцидента
            ai_text = (
                f"🧠 <b>Автоматический ИИ-анализ инцидента #{inc_id}</b> ({project}:{resource}):\n\n"
                f"{html.escape(report)}"
            )

            await self.bot.send_message(
                chat_id=chat_id,
                text=ai_text,
                parse_mode="HTML",
                reply_to_message_id=message_id
            )
            logger.info(f"Auto-AI: Post-incident analysis successfully sent for #{inc_id}")

        except Exception as e:
            logger.error(f"Auto-AI: Diagnostics failed for incident #{inc_id}: {e}")
            # Оповещаем пользователя о сбое ИИ-анализа, чтобы он не ждал бесконечно
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
        """Оповещает об успешном деплое / прохождении пайплайна"""
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
        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Notifier: Failed to send workflow success alert: {e}")

    async def on_devops_workflow_failure(self, event_type: str, data: dict) -> None:
        """Оповещает о падении CI/CD пайплайна"""
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
        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Notifier: Failed to send workflow failure alert: {e}")