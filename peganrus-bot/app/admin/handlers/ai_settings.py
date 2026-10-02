from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.common import esc, kb, show
from app.db import crud
from app.db.engine import session_factory

router = Router()


class AIFlow(StatesGroup):
    waiting_value = State()


# param -> (подпись, тип, минимум, максимум)
AI_PARAMS: dict[str, tuple[str, type, float | None, float | None]] = {
    "model": ("model", str, None, None),
    "temperature": ("temperature", float, 0.0, 2.0),
    "max_tokens": ("max_tokens", int, 100, 8000),
    "max_history_messages": ("max_history", int, 2, 100),
    "compression_threshold": ("compression_threshold", int, 4, 200),
}


class SettingError(ValueError):
    pass


async def apply_ai_setting(session: AsyncSession, key: str, raw: str) -> object:
    if key not in AI_PARAMS:
        raise SettingError(f"Неизвестный параметр: {key}")
    _, typ, low, high = AI_PARAMS[key]
    raw = raw.strip().replace(",", ".") if typ is not str else raw.strip()
    try:
        value = typ(raw)
    except ValueError:
        raise SettingError(f"Не удалось прочитать значение «{raw}» для {key}")
    if typ is str:
        if not value:
            raise SettingError("Значение не может быть пустым")
    elif (low is not None and value < low) or (high is not None and value > high):
        raise SettingError(f"{key}: допустимо от {low} до {high}")
    settings_row = await crud.get_ai_settings(session)
    setattr(settings_row, key, value)
    await session.commit()
    return value


def settings_text(s) -> str:
    return (
        "⚙️ <b>Настройки AI</b>\n"
        f"model: <code>{esc(s.model)}</code>\n"
        f"temperature: <code>{s.temperature}</code>\n"
        f"max_tokens: <code>{s.max_tokens}</code>\n"
        f"max_history: <code>{s.max_history_messages}</code>\n"
        f"compression_threshold: <code>{s.compression_threshold}</code>\n"
        f"prompt_version: <code>{esc(s.system_prompt_version)}</code>"
    )


def _menu_kb():
    return kb([("⚙️ Настройки", "ai:show"), ("✏️ Изменить", "ai:edit")], [("◀️ Назад", "menu:main")])


@router.callback_query(F.data == "menu:ai")
async def menu_ai(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await show(cb, "🤖 <b>AI</b>", _menu_kb())


@router.callback_query(F.data == "ai:show")
async def ai_show(cb: CallbackQuery) -> None:
    await cb.answer()
    async with session_factory() as session:
        s = await crud.get_ai_settings(session)
    await show(cb, settings_text(s), kb([("✏️ Изменить", "ai:edit")], [("◀️ Назад", "menu:ai")]))


@router.callback_query(F.data == "ai:edit")
async def ai_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    rows = [[(label, f"ai:set:{key}")] for key, (label, *_rest) in AI_PARAMS.items()]
    await show(cb, "Какой параметр изменить?", kb(*rows, [("◀️ Назад", "menu:ai")]))


@router.callback_query(F.data.startswith("ai:set:"))
async def ai_set(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":", 2)[2]
    if key not in AI_PARAMS:
        await cb.answer("Неизвестный параметр", show_alert=True)
        return
    async with session_factory() as session:
        current = getattr(await crud.get_ai_settings(session), key)
    _, typ, low, high = AI_PARAMS[key]
    limits = f" (от {low} до {high})" if low is not None else ""
    await state.set_state(AIFlow.waiting_value)
    await state.update_data(key=key)
    await cb.answer()
    await show(
        cb,
        f"Параметр <b>{key}</b>, сейчас: <code>{esc(str(current))}</code>\n"
        f"Отправь новое значение{limits}.",
        kb([("✖️ Отмена", "menu:ai")]),
    )


@router.message(StateFilter(AIFlow.waiting_value), F.text & ~F.text.startswith("/"))
async def ai_value(message: Message, state: FSMContext) -> None:
    key = (await state.get_data())["key"]
    async with session_factory() as session:
        try:
            value = await apply_ai_setting(session, key, message.text)
        except SettingError as e:
            await message.answer(f"❌ {esc(str(e))}\nПопробуй ещё раз или нажми Отмена.", reply_markup=kb([("✖️ Отмена", "menu:ai")]))
            return
    await state.clear()
    await message.answer(
        f"✅ {key} = <code>{esc(str(value))}</code>\nВступит в силу со следующего сообщения игроков.",
        reply_markup=_menu_kb(),
    )
