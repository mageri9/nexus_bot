from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from config import settings
from services import query_service, command_service, ai_service, incident_service, redis_client
from telegram.filters import IsAdmin

from telegram.notifier import IncidentActionCallback

router = Router()
router.message.filter(IsAdmin())


# Описываем локальный контракт для базовых callback-кнопок команды /status
class AgentActionCallback(CallbackData, prefix="agent_act"):
    agent: str
    resource: str
    action: str


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🛡️ <b>Nexus Control Terminal</b> is online.\n\n"
        "Use <code>/status</code> command to request ecosystem status.",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    sent_msg = await message.answer(
        "🔍 <i>Requesting current ecosystem state and calculating health...</i>",
        parse_mode="HTML",
    )
    try:
        keyboard_builder = InlineKeyboardBuilder()
        report_lines = ["📡 <b>Nexus Ecosystem Status</b>:\n"]

        # Получаем список всех зарегистрированных агентов из реестра
        agent_names = query_service.registry.list_agents()

        for agent_name in agent_names:
            # Читаем детальный слепок состояния агента со всеми метриками
            agent_details = await query_service.get_agent_details(agent_name)

            # Рассчитываем Health Score
            score = query_service.calculate_health_score(agent_name, agent_details)

            # Цветовой маркер общего состояния проекта
            if score >= 90:
                score_emoji = "🟢"
            elif score >= 70:
                score_emoji = "🟡"
            else:
                score_emoji = "🔴"

            report_lines.append(
                f"{score_emoji} <b>{agent_name.upper()}</b> | <b>Health: {score}%</b>"
            )

            for res_name, res_data in agent_details.items():
                if res_name == "error":
                    report_lines.append(
                        f"  └─ ❌ <code>error</code>: <code>No data from collector</code>"
                    )
                    continue

                res_status = res_data.get("status", "unknown")
                res_emoji = "✅" if res_status in ("running", "healthy") else "❌"

                # Читаем утилизацию для отображения в сводке
                metrics = res_data.get("metrics", {})
                cpu = metrics.get("cpu")
                ram = metrics.get("mem_perc")
                restarts = metrics.get("restarts", 0)

                # Метрики диска
                use_percent = metrics.get("use_percent")
                size = metrics.get("size")
                used = metrics.get("used")

                # Формируем мета-строку в зависимости от типа ресурса
                if cpu and ram:
                    meta_str = f" [CPU: {cpu} | RAM: {ram}]"
                elif use_percent:
                    meta_str = f" [Disk: {used}/{size} ({use_percent})]"
                else:
                    meta_str = ""

                restarts_str = f" (Restarts: {restarts})" if restarts > 0 else ""

                report_lines.append(
                    f"  └─ {res_emoji} <code>{res_name}</code>: <code>{res_status}</code>{meta_str}{restarts_str}"
                )

                # Добавляем инлайн-кнопку перезапуска, если поддерживается
                agent_obj = query_service.registry.get(agent_name)
                resource_obj = agent_obj.resources.get(res_name)
                if resource_obj and hasattr(resource_obj, "restart"):
                    keyboard_builder.button(
                        text=f"🔄 Restart {agent_name}:{res_name}",
                        callback_data=AgentActionCallback(
                            agent=agent_name, resource=res_name, action="restart"
                        ),
                    )
            report_lines.append("")

        keyboard_builder.adjust(1)

        await sent_msg.edit_text(
            "\n".join(report_lines),
            parse_mode="HTML",
            reply_markup=keyboard_builder.as_markup(),
        )
    except Exception as e:
        logger.exception(f"Failed to fetch system status: {e}")
        await sent_msg.edit_text(
            f"❌ <b>Failed to fetch status</b>: <code>{str(e)}</code>",
            parse_mode="HTML",
        )


@router.callback_query(AgentActionCallback.filter())
async def handle_agent_action(
    callback: types.CallbackQuery, callback_data: AgentActionCallback
):
    await callback.answer("⏳ Processing command...", show_alert=False)

    agent = callback_data.agent
    resource = callback_data.resource
    action = callback_data.action

    logger.info(f"Button pressed: agent={agent}, resource={resource}, action={action}")

    if action == "restart":
        status_msg = await callback.message.answer(
            f"🔄 <i>Requesting restart for <code>{agent}</code>:<code>{resource}</code>...</i>",
            parse_mode="HTML",
        )
        try:
            await command_service.restart_resource(agent, resource)
            await status_msg.edit_text(
                f"✅ <b>Successfully restarted</b>: <code>{agent}</code>:<code>{resource}</code>!",
                parse_mode="HTML",
            )
        except Exception as e:
            await status_msg.edit_text(
                f"❌ <b>Failed to restart</b> <code>{agent}</code>:<code>{resource}</code>\n"
                f"Error: <code>{str(e)}</code>",
                parse_mode="HTML",
            )


# Обработка нажатий на инлайн-кнопки событий инцидентов
@router.callback_query(IncidentActionCallback.filter())
async def handle_incident_callback(
    callback: types.CallbackQuery, callback_data: IncidentActionCallback
):
    await callback.answer("⏳ Processing incident action...", show_alert=False)

    inc_id = callback_data.id
    action = callback_data.act

    # Извлекаем подробную информацию об инциденте из Redis по ID
    incident = await incident_service.get_incident(inc_id)
    if not incident:
        await callback.message.reply("❌ Incident details not found in database.")
        return

    project = incident.project
    resource = incident.resource

    if action == "restart":
        status_msg = await callback.message.answer(
            f"🔄 <i>Restarting <code>{project}</code>:<code>{resource}</code> (Incident #{inc_id})...</i>",
            parse_mode="HTML"
        )
        try:
            await command_service.restart_resource(project, resource)
            await status_msg.edit_text(
                f"✅ <b>Successfully restarted</b>: <code>{project}</code>:<code>{resource}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            await status_msg.edit_text(
                f"❌ <b>Failed to restart</b> <code>{project}</code>:<code>{resource}</code>\n"
                f"Error: <code>{str(e)}</code>",
                parse_mode="HTML"
            )

    elif action == "logs":
        try:
            # Запрашиваем актуальные логи
            logs = await query_service.get_resource_logs(project, resource, limit=40)
            if len(logs) > 3500:
                logs = "...\n" + logs[-3500:]

            await callback.message.reply(
                f"📝 <b>Fresh Logs for Incident #{inc_id} ({project}:{resource}):</b>\n"
                f"<pre>{logs}</pre>",
                parse_mode="HTML"
            )
        except Exception as e:
            await callback.message.reply(f"❌ Failed to fetch logs: <code>{str(e)}</code>", parse_mode="HTML")

    elif action == "ai":
        ai_msg = await callback.message.answer(
            f"🧠 <i>Nexus AI анализирует инцидент #{inc_id} (Gemma 4)...</i>",
            parse_mode="HTML"
        )
        try:
            # Генерация отчета по конкретным логам инцидента
            system_prompt = (
                "Вы — Nexus AI, опытный DevOps-ассистент.\n"
                f"Проанализируйте логи аварии инцидента #{inc_id}.\n"
                f"Проект: {project}\n"
                f"Ресурс: {resource}\n"
                f"Количество рестартов: {incident.restart_count}\n"
                f"Логи при сбое:\n{incident.logs}\n\n"
                "Дайте краткую техническую причину аварии и 3 четких действия по исправлению на русском языке."
            )

            response = await ai_service.client.chat.completions.create(
                model=settings.AITUNNEL_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Что произошло и как это исправить?"},
                ],
                temperature=0.3,
                max_tokens=600,
            )

            report = response.choices[0].message.content

            # Кэшируем полученный отчет в теле инцидента в Redis
            incident.ai_report = report
            await redis_client.set(f"nexus:incident:detail:{inc_id}", incident.model_dump_json())

            await ai_msg.edit_text(
                f"🧠 <b>ИИ-Диагностика для инцидента #{inc_id}</b> ({project}:{resource}):\n\n{report}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"AI diagnosis for incident #{inc_id} failed: {e}")
            await ai_msg.edit_text(f"❌ Ошибка ИИ-анализа: <code>{str(e)}</code>", parse_mode="HTML")

    elif action == "silence":
        silence_key = f"nexus:silence:{project}:{resource}"
        # Выставляем TTL ключа на 1 час (3600 секунд)
        await redis_client.set(silence_key, "1", ex=3600)

        await callback.message.reply(
            f"🔕 <b>Режим тишины включен</b>\n\n"
            f"Алерты для <code>{project}</code>:<code>{resource}</code> заглушены на 1 час.",
            parse_mode="HTML"
        )


@router.message()
async def handle_ai_query(message: types.Message):
    sent_msg = await message.answer("🧠 <i>Nexus AI анализирует состояние системы (Gemma 4)...</i>", parse_mode="HTML")
    try:
        ai_response = await ai_service.analyze_system(message.text)
        await sent_msg.edit_text(ai_response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"AI Analysis failed: {e}")
        await sent_msg.edit_text(
            f"❌ <b>Ошибка ИИ-анализа</b>: <code>{str(e)}</code>\n"
            f"Убедитесь, что параметр <code>AITUNNEL_API_KEY</code> корректно настроен в <code>.env</code>.",
            parse_mode="HTML"
        )