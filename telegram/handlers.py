# telegram/handlers.py
from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from services import query_service
from telegram.filters import IsAdmin
from loguru import logger  # [1]

router = Router()

router.message.filter(IsAdmin())


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🛡️ <b>Nexus Control Terminal</b> is online.\n\n"
        "Use <code>/status</code> command to request ecosystem status.",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    # Используем HTML для стабильного форматирования
    sent_msg = await message.answer(
        "🔍 <i>Requesting current ecosystem state...</i>", parse_mode="HTML"
    )
    try:
        system_status = await query_service.get_system_status()

        report_lines = ["📡 <b>Nexus Ecosystem Status</b>:\n"]
        for agent_name, resources in system_status.items():
            all_ok = all(
                status in ("running", "healthy") for status in resources.values()
            )
            status_emoji = "🟢" if all_ok else "🔴"

            report_lines.append(f"{status_emoji} <b>{agent_name.upper()}</b>")
            for res_name, res_status in resources.items():
                res_emoji = "✅" if res_status in ("running", "healthy") else "❌"
                # Оборачиваем имена и статусы в моноширинный код <code>
                report_lines.append(
                    f"  └─ {res_emoji} <code>{res_name}</code>: <code>{res_status}</code>"
                )
            report_lines.append("")

        await sent_msg.edit_text("\n".join(report_lines), parse_mode="HTML")
    except Exception as e:
        logger.exception(f"Failed to fetch system status: {e}")
        await sent_msg.edit_text(
            f"❌ <b>Failed to fetch status</b>: <code>{str(e)}</code>",
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