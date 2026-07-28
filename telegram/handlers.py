import html
import asyncio
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command, or_f
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton
from loguru import logger

from services import (
    query_service,
    command_service,
    ai_service,
    incident_service,
    redis_client,
)
from telegram.filters import IsAdmin
from telegram.notifier import IncidentActionCallback
from telegram.callbacks import (
    AgentActionCallback,
    AgentViewCallback,
    GlobalStatsCallback,
    RootMenuCallback,
)
from telegram.views import (
    build_agent_detail_content,
    build_dashboard_content,
    build_global_stats_content,
)

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# --- КОНТРАКТЫ НАВИГАЦИИ (CALLBACK DATA) ---


# --- ОБРАБОТЧИКИ ТЕКСТОВЫХ КОМАНД ---


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    kb_builder = ReplyKeyboardBuilder()
    kb_builder.add(KeyboardButton(text="📡 Status"))

    await message.answer(
        "🛡️ <b>Nexus Control Terminal</b> is online.\n\n"
        "Use the <code>📡 Status</code> button below or type <code>/status</code> to request ecosystem status.",
        parse_mode="HTML",
        reply_markup=kb_builder.as_markup(resize_keyboard=True),
    )


@router.message(or_f(Command("status"), F.text == "📡 Status"))
async def cmd_status(message: types.Message):
    sent_msg = await message.answer(
        "🔍 <i>Requesting current ecosystem state and calculating health...</i>",
        parse_mode="HTML",
    )
    try:
        text, reply_markup = await build_dashboard_content()
        await sent_msg.edit_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Failed to fetch dashboard content: {e}")
        await sent_msg.edit_text(
            f"❌ <b>Failed to fetch status</b>: <code>{str(e)}</code>",
            parse_mode="HTML",
        )


# --- ОБРАБОТЧИКИ CALLBACK-НАВИГАЦИИ (СЛОИ UI) ---


@router.callback_query(RootMenuCallback.filter())
async def show_dashboard(callback: types.CallbackQuery):
    """Возврат на Слой 1 (Дашборд)"""
    try:
        text, reply_markup = await build_dashboard_content()
        await callback.message.edit_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to edit message to dashboard: {e}")
    await callback.answer()


@router.callback_query(AgentViewCallback.filter())
async def show_agent(callback: types.CallbackQuery, callback_data: AgentViewCallback):
    """Переход на Слой 2 (Детальная панель бота)"""
    agent_name = callback_data.agent_name
    try:
        text, reply_markup = await build_agent_detail_content(agent_name)
        await callback.message.edit_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to edit message to agent detail: {e}")
    await callback.answer()


@router.callback_query(GlobalStatsCallback.filter())
async def show_global_stats(callback: types.CallbackQuery):
    """Переход на экран аналитики ИИ и инцидентов"""
    try:
        text, reply_markup = await build_global_stats_content()
        await callback.message.edit_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to edit message to global stats: {e}")
    await callback.answer()


# --- ОБРАБОТЧИКИ ДИНАМИЧЕСКИХ ДЕЙСТВИЙ (DISPATCHER) ---


@router.callback_query(AgentActionCallback.filter())
async def process_agent_action(
    callback: types.CallbackQuery, callback_data: AgentActionCallback
):
    agent = callback_data.agent
    resource = callback_data.resource
    action = callback_data.action

    await callback.answer(f"⏳ Executing {action} on {agent}:{resource}...")

    try:
        if action == "restart":
            result = await command_service.restart_resource(agent, resource)
            await callback.message.answer(
                f"✅ <b>Restart successful:</b> <code>{agent}:{resource}</code>\n"
                f"Result ID: <code>{result[:12]}</code>",
                parse_mode="HTML",
            )
        elif action == "logs":
            logs = await query_service.get_resource_logs(agent, resource, limit=20)
            logs_preview = "\n".join(logs.strip().split("\n")[-15:])
            if len(logs_preview) > 1500:
                logs_preview = logs_preview[-1500:]

            logs_preview_escaped = html.escape(logs_preview)

            await callback.message.answer(
                f"📝 <b>Logs preview ({agent}:{resource}):</b>\n"
                f"<pre>{logs_preview_escaped or 'No logs available.'}</pre>",
                parse_mode="HTML"
            )
        elif action == "backup":
            # Заглушка бэкапа (в будущем можно вызывать асинхронную команду pg_dump/sqlite vacuum)
            await asyncio.sleep(0.5)
            await callback.message.answer(
                f"⚠️ <b>Backup Not Implemented</b>\n\n"
                f"Запрошено резервное копирование <code>{agent}:{resource}</code>.\n\n"
                f"<i>Внимание: Автоматическое создание бэкапов через UI в текущей версии Nexus не реализовано. "
                f"Пожалуйста, используйте ручной запуск pg_dump или архивацию папок на сервере во избежание утери данных.</i>",
                parse_mode="HTML"
            )
        else:
            await callback.answer(f"❌ Unknown action: {action}", show_alert=True)

    except Exception as e:
        logger.exception(f"Action failed: {e}")
        await callback.message.answer(
            f"❌ <b>Action failed:</b> <code>{agent}:{resource} ({action})</code>\n"
            f"Error: <code>{str(e)}</code>",
            parse_mode="HTML",
        )


# --- ОБРАБОТЧИК ДЕЙСТВИЙ С КАРТОЧЕК ИНЦИДЕНТОВ ---


@router.callback_query(IncidentActionCallback.filter())
async def process_incident_action(
    callback: types.CallbackQuery, callback_data: IncidentActionCallback
):
    incident_id = callback_data.id
    act = callback_data.act

    incident = await incident_service.get_incident(incident_id)
    if not incident:
        await callback.answer("❌ Incident details not found.", show_alert=True)
        return

    project = incident.project
    resource = incident.resource

    await callback.answer(f"⏳ Processing {act} for Incident #{incident_id}...")

    try:
        if act == "restart":
            result = await command_service.restart_resource(project, resource)
            await callback.message.reply(
                f"✅ <b>[Incident #{incident_id}]</b> Manual restart successful.\n"
                f"Result: <code>{result[:12]}</code>",
                parse_mode="HTML",
            )
        elif act == "logs":
            logs = await query_service.get_resource_logs(project, resource, limit=25)
            logs_preview = "\n".join(logs.strip().split("\n")[-15:])

            logs_preview_escaped = html.escape(logs_preview)

            await callback.message.reply(
                f"📝 <b>[Incident #{incident_id}] Logs:</b>\n"
                f"<pre>{logs_preview_escaped}</pre>",
                parse_mode="HTML"
            )
        elif act == "ai":
            await callback.message.reply(
                "🧠 <i>Requesting manual AI diagnostic report...</i>", parse_mode="HTML"
            )
            # Вызов единого метода вместо сборки кастомного промпта
            report = await ai_service.diagnose_incident(incident_id)
            await callback.message.reply(
                f"🧠 <b>Manual AI Diagnose for #{incident_id}:</b>\n\n{report}",
                parse_mode="HTML",
            )
        elif act == "silence":
            # Режим тишины на 1 час для конкретного ресурса
            silence_key = f"nexus:silence:{project}:{resource}"
            await redis_client.set(silence_key, "1", ex=3600)
            await callback.message.reply(
                f"🔕 Alerts silenced for 1 hour for <code>{project}:{resource}</code>.",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.exception(f"Incident action failed: {e}")
        await callback.message.reply(
            f"❌ Action failed: {str(e)}",
            parse_mode="HTML"
        )
