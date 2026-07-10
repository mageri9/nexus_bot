import html
import asyncio
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command, or_f
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.types import KeyboardButton
from loguru import logger

from config import settings
from services import (
    query_service,
    command_service,
    ai_service,
    incident_service,
    redis_client,
)
from telegram.filters import IsAdmin
from telegram.notifier import IncidentActionCallback

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# --- КОНТРАКТЫ НАВИГАЦИИ (CALLBACK DATA) ---


class AgentViewCallback(CallbackData, prefix="ag_view"):
    agent_name: str


class RootMenuCallback(CallbackData, prefix="root_menu"):
    pass


class GlobalStatsCallback(CallbackData, prefix="glob_stats"):
    pass


class AgentActionCallback(CallbackData, prefix="ag_act"):
    agent: str
    resource: str
    action: str


# --- ХЕЛПЕРЫ ФОРМАТИРОВАНИЯ ---


def format_uptime(seconds: int) -> str:
    """Форматирует секунды в человекочитаемый интервал"""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def format_size(bytes_val: int) -> str:
    """Форматирует байты в КБ / МБ / ГБ"""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    kb = bytes_val / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.1f} GB"


async def get_total_ai_usage() -> dict:
    """Безопасно сканирует хэши в Redis и суммирует потребление ИИ во всей экосистеме"""
    try:
        total_prompt = 0
        total_completion = 0
        total_requests = 0
        async for key in redis_client.scan_iter("nexus:telemetry:ai:*"):
            data = await redis_client.hgetall(key)
            total_prompt += int(data.get("prompt_tokens", 0))
            total_completion += int(data.get("completion_tokens", 0))
            total_requests += int(data.get("requests", 0))
        return {
            "prompt": total_prompt,
            "completion": total_completion,
            "total": total_prompt + total_completion,
            "requests": total_requests
        }
    except Exception as e:
        logger.error(f"Failed to calculate AI total usage: {e}")
        return {"prompt": 0, "completion": 0, "total": 0, "requests": 0}


# --- СБОРКА ТЕКСТА И КНОПОК ДЛЯ СЛОЕВ ИНТЕРФЕЙСА ---


async def build_dashboard_content() -> tuple[str, types.InlineKeyboardMarkup]:
    """Генерирует дашборд Слоя 1 (Ecosystem Overview)"""
    report_lines = ["📡 <b>Nexus Ecosystem Dashboard</b>\n"]
    agent_names = query_service.registry.list_agents()

    # Оценка здоровья по каждому агенту
    for agent_name in agent_names:
        agent_details = await query_service.get_agent_details(agent_name)
        score = query_service.calculate_health_score(agent_name, agent_details)

        # Обработка маркера инициализации
        if score == -1:
            score_emoji = "⚪"
            score_str = "N/A"
        else:
            score_str = f"{score}%"
            if score >= 90:
                score_emoji = "🟢"
            elif score >= 70:
                score_emoji = "🟡"
            else:
                score_emoji = "🔴"

        report_lines.append(f"{score_emoji} <b>{agent_name.upper()}</b> | Health: <b>{score_str}</b>")

    # 1. Извлечение емкости системного диска
    nexus_details = await query_service.get_agent_details("nexus")
    root_disk_info = "N/A"
    if nexus_details.get("version") == 2:
        root_disk_metrics = (
            nexus_details.get("storage", {}).get("root_disk", {}).get("metrics", {})
        )
        part_size_met = root_disk_metrics.get("partition_size", {})
        val = part_size_met.get("value", {})
        if val:
            root_disk_info = (
                f"{val.get('used')}/{val.get('size')} ({val.get('use_percent')})"
            )

    report_lines.append(f"💾 <b>System Disk:</b> <code>{root_disk_info}</code>")

    # 2. Агрегированные метрики токенов ИИ
    ai_usage = await get_total_ai_usage()
    report_lines.append(
        f"🧠 <b>AI Metrics:</b> <code>{ai_usage['total']}</code> tokens (~{ai_usage['requests']} reqs)"
    )

    # 3. Хронология последних событий (Топ-3)
    report_lines.append("\n📜 <b>Recent Events:</b>")
    timeline = await incident_service.get_timeline(3)
    if timeline:
        severity_emojis = {
            "HIGH": "🚨",
            "MEDIUM": "⚠️",
            "SUCCESS": "✅",
            "INFO": "ℹ️",
            "WARNING": "⚠️",
        }
        for event in timeline:
            try:
                dt = datetime.fromisoformat(event["timestamp"])
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = "N/A"
            emoji = severity_emojis.get(event["severity"], "ℹ️")
            report_lines.append(f"  {emoji} <code>[{time_str}]</code> {event['text']}")
    else:
        report_lines.append("  <i>No events logged today.</i>")

    # Формирование инлайн-кнопок
    keyboard_builder = InlineKeyboardBuilder()
    for agent_name in agent_names:
        keyboard_builder.button(
            text=f"🤖 {agent_name.upper()}",
            callback_data=AgentViewCallback(agent_name=agent_name),
        )
    keyboard_builder.button(
        text="📊 AI & Incident Stats", callback_data=GlobalStatsCallback()
    )
    keyboard_builder.adjust(2, 2, 1)

    return "\n".join(report_lines), keyboard_builder.as_markup()


async def build_agent_detail_content(agent_name: str) -> tuple[str, types.InlineKeyboardMarkup]:
    """Генерирует детальную панель Слоя 2 (Agent Drill-down)"""
    agent_details = await query_service.get_agent_details(agent_name)
    score = query_service.calculate_health_score(agent_name, agent_details)

    # Обработка маркера инициализации
    if score == -1:
        score_emoji = "⚪"
        score_str = "N/A"
    else:
        score_str = f"{score}%"
        if score >= 90:
            score_emoji = "🟢"
        elif score >= 70:
            score_emoji = "🟡"
        else:
            score_emoji = "🔴"

    report_lines = [
        f"{score_emoji} <b>{agent_name.upper()} Agent Panel</b>",
        f"Health Score: <b>{score_str}</b>\n",
        "<b>Status Details:</b>"
    ]

    keyboard_builder = InlineKeyboardBuilder()

    if agent_details.get("version") != 2:
        report_lines.append("  ❌ <i>No cached State V2 telemetry available yet.</i>")
        keyboard_builder.button(
            text="🔙 Back to Dashboard", callback_data=RootMenuCallback()
        )
        return "\n".join(report_lines), keyboard_builder.as_markup()

    # Сборка контейнеров
    containers = agent_details.get("containers", {})
    for name, data in containers.items():
        status = data.get("status", "unknown")
        res_emoji = "✅" if status in ("running", "healthy") else "❌"
        report_lines.append(
            f"├─ {res_emoji} <code>{name}</code>: <code>{status}</code>"
        )

        metrics = data.get("metrics", {})
        cpu_val = metrics.get("cpu", {}).get("value", "0.00%")
        mem_val = metrics.get("mem_perc", {}).get("value", "0.00%")
        memory_val = metrics.get("memory", {}).get("value", "0MiB")
        restarts_val = metrics.get("restarts", {}).get("value", 0)
        uptime_seconds = metrics.get("uptime_seconds", {}).get("value", 0)

        uptime_formatted = (
            format_uptime(uptime_seconds) if status in ("running", "healthy") else "N/A"
        )
        restarts_str = f" | Restarts: {restarts_val}" if restarts_val > 0 else ""

        report_lines.append(f"│  └─ CPU: {cpu_val} | RAM: {memory_val} ({mem_val})")
        report_lines.append(f"│  └─ Uptime: {uptime_formatted}{restarts_str}")

        # Динамическая прорисовка кнопок действий на основе возможностей (capabilities) ресурса
        capabilities = data.get("capabilities", [])
        row_buttons = []
        if "restart" in capabilities:
            row_buttons.append(
                types.InlineKeyboardButton(
                    text=f"🔄 Restart {name}",
                    callback_data=AgentActionCallback(
                        agent=agent_name, resource=name, action="restart"
                    ).pack(),
                )
            )
        if "logs" in capabilities:
            row_buttons.append(
                types.InlineKeyboardButton(
                    text=f"📝 Logs {name}",
                    callback_data=AgentActionCallback(
                        agent=agent_name, resource=name, action="logs"
                    ).pack(),
                )
            )
        if row_buttons:
            keyboard_builder.row(*row_buttons)

    # Сборка накопителей (дисков/папок)
    storage = agent_details.get("storage", {})
    for name, data in storage.items():
        status = data.get("status", "unknown")
        res_emoji = "✅" if status in ("running", "healthy") else "❌"
        report_lines.append(
            f"└─ {res_emoji} <code>{name}</code>: <code>{status}</code>"
        )

        metrics = data.get("metrics", {})
        if "directory_size" in metrics:
            bytes_val = metrics["directory_size"].get("value", 0)
            formatted_size = format_size(bytes_val)
            report_lines.append(f"   └─ Directory Size: {formatted_size}")
        elif "partition_size" in metrics:
            val = metrics["partition_size"].get("value", {})
            report_lines.append(
                f"   └─ Partition: {val.get('used')}/{val.get('size')} ({val.get('use_percent')})"
            )

        capabilities = data.get("capabilities", [])
        if "backup_db" in capabilities:
            keyboard_builder.button(
                text=f"📥 Backup {name}",
                callback_data=AgentActionCallback(
                    agent=agent_name, resource=name, action="backup"
                ),
            )

    # Кнопка возврата в меню
    keyboard_builder.row(
        types.InlineKeyboardButton(
            text="🔙 Back to Dashboard", callback_data=RootMenuCallback().pack()
        )
    )

    return "\n".join(report_lines), keyboard_builder.as_markup()


async def build_global_stats_content() -> tuple[str, types.InlineKeyboardMarkup]:
    """Генерирует экран сквозной аналитики ИИ и инцидентов через безопасный SCAN"""
    report_lines = [
        "📊 <b>Ecosystem AI & Incident Analytics</b>\n",
        "<b>AI Consumption Statistics (Total):</b>",
    ]

    has_keys = False
    # Безопасное сканирование ключей телеметрии
    async for key in redis_client.scan_iter("nexus:telemetry:ai:*"):
        has_keys = True
        parts = key.split(":")

        # Индексы: 0:nexus, 1:telemetry, 2:ai, 3:project, 4:provider, 5:model, 6:modality
        project = parts[3].upper() if len(parts) >= 4 else "UNKNOWN"
        provider = parts[4] if len(parts) >= 5 else "UNKNOWN"
        model = parts[5] if len(parts) >= 6 else "UNKNOWN"
        modality = (
            parts[6] if len(parts) >= 7 else "text"
        )

        data = await redis_client.hgetall(key)
        prompt = int(data.get("prompt_tokens", 0))
        completion = int(data.get("completion_tokens", 0))
        reqs = int(data.get("requests", 0))

        # Выводим модальность (text / vision) рядом с информацией о модели
        report_lines.append(f"├─ <b>{project}</b> (via <code>{provider}/{model}</code> | <i>{modality}</i>)")
        report_lines.append(f"│  └─ Prompt: {prompt} | Compl: {completion} (Reqs: {reqs})")

    if not has_keys:
        report_lines.append("  <i>No AI usage stats found.</i>")

    report_lines.append("\n<b>Recent Incidents (History):</b>")
    recent_incidents = await incident_service.list_recent_incidents(5)
    if recent_incidents:
        for inc in recent_incidents:
            status_emoji = "🟢 Resolved" if inc.status == "resolved" else "🔴 Open"
            opened = inc.opened_at.strftime("%Y-%m-%d %H:%M")
            duration_str = f" ({inc.duration:.1f}s)" if inc.duration else ""
            report_lines.append(
                f"├─ #{inc.id} | <b>{inc.project}:{inc.resource}</b>\n"
                f"│  └─ Status: {status_emoji} | Opened: {opened}{duration_str}"
            )
    else:
        report_lines.append("  <i>No incidents in database.</i>")

    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Back to Dashboard", callback_data=RootMenuCallback())
    return "\n".join(report_lines), kb.as_markup()


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
            system_prompt = (
                f"Вы — Nexus AI. Проанализируйте аварию для инцидента #{incident_id}.\n"
                f"Проект: {project}\nСервис: {resource}\nЛоги:\n{incident.logs}"
            )
            report = await ai_service.analyze_system(system_prompt)
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