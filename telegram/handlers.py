from aiogram import Router, types, F
from aiogram.filters import (
    CommandStart,
    Command,
    or_f,
)
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import (
    InlineKeyboardBuilder,
    ReplyKeyboardBuilder,
)
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

# Импортируем контракт инцидентов из нотификатора
from telegram.notifier import IncidentActionCallback

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# Описываем локальный контракт для базовых callback-кнопок команды /status
class AgentActionCallback(CallbackData, prefix="agent_act"):
    agent: str
    resource: str
    action: str


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    # Строим постоянную меню-клавиатуру (Reply Keyboard) [1]
    kb_builder = ReplyKeyboardBuilder()
    kb_builder.add(KeyboardButton(text="📡 Status"))

    await message.answer(
        "🛡️ <b>Nexus Control Terminal</b> is online.\n\n"
        "Use the <code>📡 Status</code> button below or type <code>/status</code> to request ecosystem status.",
        parse_mode="HTML",
        # Свойство resize_keyboard делает кнопку компактной и аккуратной
        reply_markup=kb_builder.as_markup(resize_keyboard=True),
    )


# Добавляем фильтр or_f: срабатывает и на команду /status, и на текстовую кнопку [1]
@router.message(or_f(Command("status"), F.text == "📡 Status"))
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

# (Остальной код хэндлеров кнопок и ИИ-анализа остается без изменений)