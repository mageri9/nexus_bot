"""
telegram/handlers_onboarding.py

Первый FSM-флоу в проекте — остальные хендлеры (telegram/handlers.py) работают
чисто на callback_data без состояний. Storage для FSM должен быть RedisStorage
(не MemoryStorage), чтобы состояние визарда переживало рестарт бота — у тебя
уже есть готовый redis_client в services/__init__.py, просто прокинь его при
создании Dispatcher в main.py:

    from aiogram.fsm.storage.redis import RedisStorage
    from services import redis_client
    dp = Dispatcher(storage=RedisStorage(redis=redis_client))

Роутер регистрируется в main.py так же, как router из telegram/handlers.py.
"""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from telegram.filters import IsAdmin
from services import onboarding as onboarding_service

router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class NewProjectStates(StatesGroup):
    waiting_name = State()
    selecting_containers = State()
    waiting_disk_path = State()
    confirming = State()


class ContainerToggleCallback(CallbackData, prefix="np_toggle"):
    container: str


class ContainersDoneCallback(CallbackData, prefix="np_done"):
    pass


class SkipDiskCallback(CallbackData, prefix="np_skip_disk"):
    pass


class HeartbeatCallback(CallbackData, prefix="np_hb"):
    enabled: bool


class ConfirmSaveCallback(CallbackData, prefix="np_confirm"):
    pass


class CancelCallback(CallbackData, prefix="np_cancel"):
    pass


def _containers_keyboard(candidates: list[str], selected: set[str]) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for name in candidates:
        mark = "✅" if name in selected else "⬜"
        kb.button(text=f"{mark} {name}", callback_data=ContainerToggleCallback(container=name))
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="➡️ Готово", callback_data=ContainersDoneCallback().pack()))
    kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data=CancelCallback().pack()))
    return kb.as_markup()


@router.message(Command("newproject"))
async def cmd_new_project(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(NewProjectStates.waiting_name)
    await message.answer(
        "🆕 <b>Подключение нового проекта</b>\n\n"
        "Напиши имя проекта (как оно будет отображаться в дашборде, например <code>my_new_bot</code>).",
        parse_mode="HTML",
    )


@router.message(NewProjectStates.waiting_name)
async def process_project_name(message: types.Message, state: FSMContext):
    project_name = message.text.strip().lower()
    if not project_name or " " in project_name:
        await message.answer("⚠️ Имя не должно быть пустым или содержать пробелы. Попробуй ещё раз.")
        return

    if await onboarding_service.agent_store.agent_exists(project_name):
        await message.answer(
            f"⚠️ Проект <code>{project_name}</code> уже зарегистрирован в Nexus. Введи другое имя.",
            parse_mode="HTML",
        )
        return

    sent = await message.answer("🔍 <i>Ищу контейнеры...</i>", parse_mode="HTML")

    try:
        candidates = await onboarding_service.discover_containers(project_name)
    except RuntimeError as e:
        # local_shell.py бросает RuntimeError при ненулевом коде возврата docker-команды
        # (демон недоступен, нет прав и т.п.) — такое не должно ронять хендлер трейсбеком.
        logger.error(f"Onboarding: docker discovery failed: {e}")
        await sent.edit_text(f"❌ Не удалось опросить docker: <code>{str(e)}</code>", parse_mode="HTML")
        await state.clear()
        return

    if not candidates:
        await sent.edit_text("❌ Не найдено ни одного docker-контейнера на хосте. Отмена.")
        await state.clear()
        return

    await state.update_data(project_name=project_name, candidates=candidates, selected=[])
    await state.set_state(NewProjectStates.selecting_containers)

    await sent.edit_text(
        f"📦 <b>Проект:</b> <code>{project_name}</code>\n\n"
        f"Выбери контейнеры, относящиеся к этому проекту (тапни, чтобы отметить):",
        parse_mode="HTML",
        reply_markup=_containers_keyboard(candidates, set()),
    )


@router.callback_query(ContainerToggleCallback.filter(), NewProjectStates.selecting_containers)
async def toggle_container(callback: types.CallbackQuery, callback_data: ContainerToggleCallback, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("selected", []))
    container = callback_data.container

    if container in selected:
        selected.discard(container)
    else:
        selected.add(container)

    await state.update_data(selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=_containers_keyboard(data["candidates"], selected)
    )
    await callback.answer()


@router.callback_query(ContainersDoneCallback.filter(), NewProjectStates.selecting_containers)
async def containers_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected", [])
    if not selected:
        await callback.answer("Выбери хотя бы один контейнер.", show_alert=True)
        return

    await state.set_state(NewProjectStates.waiting_disk_path)

    # Пытаемся предложить дефолтный disk path по первому контейнеру
    suggested_path = await onboarding_service.get_container_mount_path(selected[0])

    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Без диска", callback_data=SkipDiskCallback())
    kb.button(text="❌ Отмена", callback_data=CancelCallback())
    kb.adjust(1)

    hint = f"\n\nНайден вероятный путь: <code>{suggested_path}</code> — можешь прислать его же или другой." if suggested_path else ""
    await callback.message.edit_text(
        f"💾 Пришли путь для мониторинга диска проекта (или нажми «Без диска»).{hint}",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(SkipDiskCallback.filter(), NewProjectStates.waiting_disk_path)
async def skip_disk(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(disk_path=None)
    await _ask_heartbeat(callback.message, state)
    await callback.answer()


@router.message(NewProjectStates.waiting_disk_path)
async def process_disk_path(message: types.Message, state: FSMContext):
    await state.update_data(disk_path=message.text.strip())
    await _ask_heartbeat(message, state)


async def _ask_heartbeat(message: types.Message, state: FSMContext):
    await state.set_state(NewProjectStates.confirming)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Включить heartbeat", callback_data=HeartbeatCallback(enabled=True))
    kb.button(text="🚫 Без heartbeat", callback_data=HeartbeatCallback(enabled=False))
    kb.adjust(1)
    await message.answer(
        "💓 Включить heartbeat-мониторинг (проект должен слать пульсы через nexus_sdk)?",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(HeartbeatCallback.filter(), NewProjectStates.confirming)
async def process_heartbeat_choice(callback: types.CallbackQuery, callback_data: HeartbeatCallback, state: FSMContext):
    data = await state.get_data()

    rows = await onboarding_service.build_resource_rows(
        project_name=data["project_name"],
        selected_containers=data["selected"],
        disk_path=data.get("disk_path"),
        enable_heartbeat=callback_data.enabled,
    )
    await state.update_data(resource_rows=rows)

    preview_lines = [f"📋 <b>Превью манифеста для «{data['project_name']}»:</b>\n"]
    for row in rows:
        cfg_str = ", ".join(f"{k}={v}" for k, v in row["config"].items())
        preview_lines.append(f"• <code>{row['resource_key']}</code> ({row['resource_type']}): {cfg_str}")

    kb = InlineKeyboardBuilder()
    kb.button(text="💾 Сохранить", callback_data=ConfirmSaveCallback())
    kb.button(text="❌ Отмена", callback_data=CancelCallback())
    kb.adjust(1)

    await callback.message.edit_text(
        "\n".join(preview_lines), parse_mode="HTML", reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(ConfirmSaveCallback.filter(), NewProjectStates.confirming)
async def confirm_save(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await callback.answer("⏳ Сохраняю и регистрирую...")

    try:
        agent = await onboarding_service.commit_new_project(data["project_name"], data["resource_rows"])
        await callback.message.edit_text(
            f"✅ <b>Проект «{agent.name}» подключён.</b>\n\n"
            f"Ресурсы: <code>{', '.join(agent.resources.keys())}</code>\n\n"
            f"Появится в дашборде на следующем тике сборщика (до {5} сек), рестарт Nexus не требуется.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"Onboarding: failed to commit new project: {e}")
        await callback.message.edit_text(f"❌ Ошибка при сохранении: <code>{str(e)}</code>", parse_mode="HTML")
    finally:
        await state.clear()


@router.callback_query(CancelCallback.filter())
async def cancel_onboarding(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🚫 Подключение проекта отменено.")
    await callback.answer()