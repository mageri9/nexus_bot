from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from services import query_service
from telegram.filters import IsAdmin

router = Router()

# Защищаем абсолютно все хэндлеры этого роутера фильтром IsAdmin [1]
router.message.filter(IsAdmin())


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "🛡️ *Nexus Control Terminal* is online.\n\n"
        "Use /status command to request ecosystem status.",
        parse_mode="Markdown",
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    sent_msg = await message.answer(
        "🔍 *Requesting current ecosystem state...*", parse_mode="Markdown"
    )
    try:
        # Получаем данные через сервисный слой [1]
        system_status = await query_service.get_system_status()

        report_lines = ["📡 *Nexus Ecosystem Status*:\n"]

        for agent_name, resources in system_status.items():
            # Если хотя бы один ресурс упал — проект помечается красным
            all_ok = all(
                status in ("running", "healthy") for status in resources.values()
            )
            status_emoji = "🟢" if all_ok else "🔴"

            report_lines.append(f"{status_emoji} *{agent_name.upper()}*")
            for res_name, res_status in resources.items():
                res_emoji = "✅" if res_status in ("running", "healthy") else "❌"
                report_lines.append(f" └─ {res_emoji} {res_name}: `{res_status}`")
            report_lines.append("")  # Разделитель между проектами

        await sent_msg.edit_text("\n".join(report_lines), parse_mode="Markdown")
    except Exception as e:
        await sent_msg.edit_text(
            f"❌ *Failed to fetch status*: {str(e)}", parse_mode="Markdown"
        )