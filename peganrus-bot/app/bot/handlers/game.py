import asyncio
import contextlib
import logging
import re

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import update

from app.ai import deepseek
from app.ai.compressor import schedule_compression
from app.ai.context_builder import build_messages
from app.ai.deepseek import AIResult, DeepSeekError
from app.ai.state_handler import (
    apply_fallback_state,
    apply_tool_calls,
    extract_state_block,
)
from app.bot.tools import TOOLS
from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import MessageHistory, User

log = logging.getLogger(__name__)
router = Router()

AI_UNAVAILABLE = "⚠️ AI временно недоступен. Попробуй ещё раз через минуту."
TRUNCATED_NOTICE = "\n\n⚠️ [Ответ обрезан. Напиши «дальше»]"

_OPTION_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+?)\s*$")
_locks: dict[int, asyncio.Lock] = {}


def parse_options(text: str) -> dict[int, str] | None:
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = _OPTION_RE.match(line)
        if m:
            found[int(m.group(1))] = m.group(2)
    return found if set(found) == {1, 2, 3, 4} else None


def options_keyboard(options: dict[int, str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{n}. {_short(options[n])}", callback_data=f"game:opt:{n}")]
        for n in (1, 2, 3, 4)
    ]
    rows.append([InlineKeyboardButton(text="✏️ Свой вариант", callback_data="game:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short(text: str, limit: int = 55) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def split_message(text: str, limit: int | None = None) -> list[str]:
    limit = limit or settings.TELEGRAM_CHUNK_LENGTH
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > limit:
            cut = max(paragraph.rfind("\n", 0, limit), paragraph.rfind(". ", 0, limit) + 1, paragraph.rfind(" ", 0, limit))
            cut = cut if cut > limit // 2 else limit
            piece, paragraph = paragraph[:cut].rstrip(), paragraph[cut:].lstrip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks or [""]


@contextlib.asynccontextmanager
async def typing_indicator(bot: Bot, chat_id: int):
    async def loop() -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id, "typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def is_busy(user_id: int) -> bool:
    lock = _locks.get(user_id)
    return bool(lock and lock.locked())


async def run_turn(bot: Bot, chat_id: int, user_id: int, text: str) -> None:
    lock = _locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        await bot.send_message(chat_id, "⏳ Мастер ещё отвечает на предыдущее сообщение.")
        return
    async with lock:
        try:
            await _turn(bot, chat_id, user_id, text)
        except Exception:
            log.exception("Необработанная ошибка хода user_id=%s", user_id)
            await bot.send_message(chat_id, AI_UNAVAILABLE)


async def _ask_ai(messages: list[dict], model: str, temperature: float, max_tokens: int) -> AIResult:
    result = await deepseek.chat(
        messages, model=model, temperature=temperature, max_tokens=max_tokens,
        tools=TOOLS, tool_choice="auto",
    )
    if result.tool_calls and not result.content:
        # Модель только вызвала функции: просим отдельно написать текст для игрока.
        follow = messages + [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": (result.raw_message or {}).get("tool_calls"),
            },
            *[
                {"role": "tool", "tool_call_id": tc.id, "content": "ok"}
                for tc in result.tool_calls
            ],
        ]
        try:
            second = await deepseek.chat(
                follow, model=model, temperature=temperature, max_tokens=max_tokens,
                tools=TOOLS, tool_choice="none",
            )
            result.content = second.content
            result.finish_reason = second.finish_reason
            result.add_usage(second)
        except DeepSeekError:
            log.warning("Не удалось получить текст после вызова функций")
    return result


async def _turn(bot: Bot, chat_id: int, user_id: int, text: str) -> None:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            return
        ai = await crud.get_ai_settings(session)  # настройки читаются при КАЖДОМ запросе
        ch = await crud.get_or_create_character(session, user_id)
        messages = await build_messages(session, user, ch, text, ai.max_history_messages)
        model, temperature, max_tokens = ai.model, ai.temperature, ai.max_tokens
        await session.commit()

    try:
        async with typing_indicator(bot, chat_id):
            result = await _ask_ai(messages, model, temperature, max_tokens)
    except DeepSeekError:
        await bot.send_message(chat_id, AI_UNAVAILABLE)
        return

    reply, state_block = extract_state_block(result.content)
    if not reply:
        reply = "Мастер на мгновение задумался. Напиши «дальше», чтобы продолжить."

    async with session_factory() as session:
        ch = await crud.get_or_create_character(session, user_id)
        applied: list[str] = []
        try:
            async with session.begin_nested():
                applied = await apply_tool_calls(session, user_id, ch, result.tool_calls)
                if not applied and state_block:
                    applied = await apply_fallback_state(session, user_id, ch, state_block)
        except Exception:
            log.exception("Не удалось применить состояние user_id=%s", user_id)
        log.info("user_id=%s применено: %s, токены: %s", user_id, applied, result.total_tokens)

        session.add(MessageHistory(user_id=user_id, role="user", content=text))
        session.add(
            MessageHistory(
                user_id=user_id,
                role="assistant",
                content=reply,
                tokens_used=result.total_tokens,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cache_hit_tokens=result.cache_hit_tokens,
            )
        )
        await session.execute(
            update(User).where(User.id == user_id).values(messages_today=User.messages_today + 1)
        )
        await session.commit()

    shown = reply + (TRUNCATED_NOTICE if result.finish_reason == "length" else "")
    options = parse_options(reply)
    chunks = split_message(shown)
    for i, chunk in enumerate(chunks):
        markup = options_keyboard(options) if options and i == len(chunks) - 1 else None
        await bot.send_message(chat_id, chunk, reply_markup=markup)

    schedule_compression(user_id)


@router.callback_query(F.data == "game:start")
async def cb_start(cb: CallbackQuery, bot: Bot, user: User) -> None:
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await run_turn(bot, cb.message.chat.id, user.id, settings.START_GAME_TEXT)


@router.callback_query(F.data == "game:custom")
async def cb_custom(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer(f"✏️ {settings.CUSTOM_OPTION_HINT}")


@router.callback_query(F.data.startswith("game:opt:"))
async def cb_option(cb: CallbackQuery, bot: Bot, user: User) -> None:
    if is_busy(user.id):
        await cb.answer("⏳ Мастер ещё отвечает", show_alert=False)
        return
    number = int(cb.data.rsplit(":", 1)[1])
    async with session_factory() as session:
        last = await crud.last_assistant_message(session, user.id)
    options = parse_options(last or "")
    if not options or number not in options:
        await cb.answer("Эти варианты устарели — напиши действие текстом.", show_alert=True)
        return
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    choice = options[number]
    await bot.send_message(cb.message.chat.id, f"▶️ {choice}")
    await run_turn(bot, cb.message.chat.id, user.id, choice)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, bot: Bot, user: User) -> None:
    await run_turn(bot, message.chat.id, user.id, message.text)
