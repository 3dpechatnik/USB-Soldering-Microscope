import io

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.admin.common import esc, kb, show
from app.db.engine import session_factory
from app.db.models import PromptModule

router = Router()

PREVIEW_CHARS = 2500
MAX_UPLOAD_BYTES = 1_000_000


class ModuleFlow(StatesGroup):
    waiting_content = State()


@router.callback_query(F.data == "menu:modules")
async def menu_modules(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    async with session_factory() as session:
        modules = (
            await session.scalars(select(PromptModule).order_by(PromptModule.sort_order, PromptModule.id))
        ).all()
    rows = [
        [(f"{'✅' if m.is_active else '❌'} {m.name}", f"md:view:{m.id}")] for m in modules
    ]
    await show(
        cb,
        "🧩 <b>Модули промпта</b>\n"
        "always/character_creation/combat/rest/trading подключаются к системному промпту; "
        "manual — служебные тексты (политика, справка, промпты сжатия и анализа).",
        kb(*rows, [("◀️ Назад", "menu:main")]),
    )


async def _view(cb: CallbackQuery, module_id: int) -> None:
    async with session_factory() as session:
        m = await session.get(PromptModule, module_id)
    if m is None:
        await show(cb, "Модуль не найден.", kb([("◀️ Назад", "menu:modules")]))
        return
    content = m.content or ""
    preview = esc(content[:PREVIEW_CHARS]) if content else "(пусто)"
    more = f"\n… (показано {PREVIEW_CHARS} из {len(content)} символов)" if len(content) > PREVIEW_CHARS else ""
    text = (
        f"🧩 <b>{esc(m.name)}</b> — {m.trigger_type} — {'✅ вкл' if m.is_active else '❌ выкл'}\n"
        f"Символов: {len(content)}\n\n<pre>{preview}</pre>{more}"
    )
    toggle = "❌ Выключить" if m.is_active else "✅ Включить"
    await show(
        cb,
        text,
        kb(
            [("✏️ Редактировать", f"md:edit:{m.id}"), (toggle, f"md:toggle:{m.id}")],
            [("📄 Скачать файлом", f"md:file:{m.id}")],
            [("◀️ Назад", "menu:modules")],
        ),
    )


@router.callback_query(F.data.startswith("md:view:"))
async def view_module(cb: CallbackQuery) -> None:
    await cb.answer()
    await _view(cb, int(cb.data.rsplit(":", 1)[1]))


@router.callback_query(F.data.startswith("md:toggle:"))
async def toggle_module(cb: CallbackQuery) -> None:
    module_id = int(cb.data.rsplit(":", 1)[1])
    async with session_factory() as session:
        m = await session.get(PromptModule, module_id)
        if m is not None:
            m.is_active = not m.is_active
            await session.commit()
    await cb.answer("Готово")
    await _view(cb, module_id)


@router.callback_query(F.data.startswith("md:file:"))
async def module_file(cb: CallbackQuery) -> None:
    await cb.answer()
    async with session_factory() as session:
        m = await session.get(PromptModule, int(cb.data.rsplit(":", 1)[1]))
    if m is None:
        return
    data = (m.content or "").encode("utf-8")
    await cb.message.answer_document(BufferedInputFile(data or b" ", filename=f"{m.name}.txt"))


@router.callback_query(F.data.startswith("md:edit:"))
async def edit_module(cb: CallbackQuery, state: FSMContext) -> None:
    module_id = int(cb.data.rsplit(":", 1)[1])
    await state.set_state(ModuleFlow.waiting_content)
    await state.update_data(module_id=module_id)
    await cb.answer()
    await show(
        cb,
        "✏️ Отправь новый текст модуля сообщением.\n"
        "Длинный текст (больше 4096 символов) отправь файлом .txt в кодировке UTF-8.\n"
        "Чтобы очистить модуль, отправь один символ «-».",
        kb([("✖️ Отмена", f"md:view:{module_id}")]),
    )


@router.message(StateFilter(ModuleFlow.waiting_content), (F.text & ~F.text.startswith("/")) | F.document)
async def save_module(message: Message, state: FSMContext, bot: Bot) -> None:
    module_id = (await state.get_data())["module_id"]
    if message.document:
        if (message.document.file_size or 0) > MAX_UPLOAD_BYTES:
            await message.answer("Файл слишком большой.")
            return
        buf = io.BytesIO()
        await bot.download(message.document, destination=buf)
        try:
            content = buf.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            await message.answer("Не удалось прочитать файл: нужна кодировка UTF-8.")
            return
    else:
        content = message.text
    content = "" if content.strip() == "-" else content.strip()

    async with session_factory() as session:
        m = await session.get(PromptModule, module_id)
        if m is None:
            await state.clear()
            await message.answer("Модуль не найден.")
            return
        m.content = content
        await session.commit()
        name = m.name
    await state.clear()
    await message.answer(
        f"✅ Модуль <b>{esc(name)}</b> обновлён ({len(content)} символов).",
        reply_markup=kb([("🧩 К модулям", "menu:modules")]),
    )
