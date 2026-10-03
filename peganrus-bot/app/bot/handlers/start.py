from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import User

router = Router()


def _doc_rows() -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    if settings.TERMS_URL:
        rows.append([InlineKeyboardButton(text="📄 Пользовательское соглашение", url=settings.TERMS_URL)])
    if settings.PRIVACY_URL:
        rows.append([InlineKeyboardButton(text="🔒 Политика конфиденциальности", url=settings.PRIVACY_URL)])
    return rows


def _agree_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *_doc_rows(),
            [InlineKeyboardButton(text="✅ Мне есть 18, согласен(на)", callback_data="agree")],
        ]
    )


def _docs_kb() -> InlineKeyboardMarkup | None:
    rows = _doc_rows()
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _play_kb(text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="game:start")]])


async def _privacy_text() -> str:
    async with session_factory() as session:
        return await crud.get_text(session, "text_privacy")


async def _do_agree(user: User) -> tuple[str, InlineKeyboardMarkup]:
    async with session_factory() as session:
        db_user = await session.get(User, user.id)
        first_time = not db_user.agreed_to_privacy
        if first_time:
            db_user.agreed_to_privacy = True
            db_user.privacy_agreed_at = crud.utcnow()
            await crud.log_security(
                session, "privacy_agreed", db_user.id, db_user.telegram_id, {"username": db_user.username}
            )
        character = await crud.get_or_create_character(session, db_user.id)
        created = character.is_created
        welcome = await crud.get_text(session, "text_welcome")
        await session.commit()
    if created:
        return "✅ Согласие уже принято. Продолжаем приключение — просто напиши, что делает герой.", _play_kb(
            "▶️ Продолжить"
        )
    return welcome, _play_kb("🎲 Начать игру")


@router.message(Command("start"))
async def cmd_start(message: Message, user: User) -> None:
    if not user.agreed_to_privacy:
        await message.answer(await _privacy_text(), reply_markup=_agree_kb())
        return
    text, kb = await _do_agree(user)
    await message.answer(f"С возвращением, {message.from_user.first_name or 'путник'}!\n\n{text}", reply_markup=kb)


@router.message(Command("agree"))
async def cmd_agree(message: Message, user: User) -> None:
    text, kb = await _do_agree(user)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "agree")
async def cb_agree(cb: CallbackQuery, user: User) -> None:
    await cb.answer()
    text, kb = await _do_agree(user)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(text, reply_markup=kb)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    await message.answer(await _privacy_text(), reply_markup=_docs_kb())
