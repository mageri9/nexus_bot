from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger  # [1]

from services import query_service, command_service
from telegram.filters import IsAdmin

router = Router()
router.message.filter(IsAdmin())


# 1. Описываем строгий контракт для callback-кнопок [1]
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
        "🔍 <i>Requesting current ecosystem state...</i>", parse_mode="HTML"
    )
    try:
        system_status = await query_service.get_system_status()

        # Строим клавиатуру динамически [1]
        keyboard_builder = InlineKeyboardBuilder()
        report_lines = ["📡 <b>Nexus Ecosystem Status</b>:\n"]

        for agent_name, resources in system_status.items():
            all_ok = all(
                status in ("running", "healthy") for status in resources.values()
            )
            status_emoji = "🟢" if all_ok else "🔴"

            report_lines.append(f"{status_emoji} <b>{agent_name.upper()}</b>")
            for res_name, res_status in resources.items():
                res_emoji = "✅" if res_status in ("running", "healthy") else "❌"
                report_lines.append(
                    f"  └─ {res_emoji} <code>{res_name}</code>: <code>{res_status}</code>"
                )

                # Если ресурс поддерживает перезапуск, динамически добавляем инлайн-кнопку
                agent_obj = query_service.registry.get(agent_name)
                resource_obj = agent_obj.resources[res_name]
                if hasattr(resource_obj, "restart"):
                    keyboard_builder.button(
                        text=f"🔄 Restart {agent_name}:{res_name}",
                        callback_data=AgentActionCallback(
                            agent=agent_name, resource=res_name, action="restart"
                        ),
                    )
            report_lines.append("")

        # Форматируем расположение кнопок (по одной в ряд)
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


# 2. Обрабатываем нажатие кнопки перезапуска [1]
@router.callback_query(AgentActionCallback.filter())
async def handle_agent_action(
    callback: types.CallbackQuery, callback_data: AgentActionCallback
):
    # Показываем системную плашку загрузки в Telegram
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
            # Вызываем команду перезапуска через сервисный слой [1]
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


@router.message()
async def debug_catch_all(message: types.Message):
    logger.debug(f"DEBUG CATCH-ALL: Received text: '{message.text}'")
    try:
        await message.answer(
            f"🤖 Получил твой текст: '<code>{message.text}</code>'. Но он не совпал с командами.",
            parse_mode="HTML",
        )
        logger.success("DEBUG CATCH-ALL: Echo reply sent successfully!")
    except Exception as e:
        logger.error(f"DEBUG CATCH-ALL: Failed to send reply: {e}")