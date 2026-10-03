import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.admin.common import esc, kb
from app.admin.handlers.ai_settings import AI_PARAMS, SettingError, apply_ai_setting
from app.ai import deepseek
from app.ai.deepseek import DeepSeekError
from app.db import crud
from app.db.engine import session_factory
from app.db.models import GameAnalytics

log = logging.getLogger(__name__)
router = Router()

_SETTING_RE = re.compile(r"\[SETTING\s+([a-z_]+)\s*=\s*([^\]\s]+)\s*\]", re.IGNORECASE)
TELEGRAM_LIMIT = 3800


def summarize_period(rows: list[GameAnalytics], days: int) -> str:
    if not rows:
        return f"За {days} дн.: данных нет."
    first, last = rows[0], rows[-1]
    prompt = sum(r.prompt_tokens for r in rows)
    hit = sum(r.cache_hit_tokens for r in rows)
    tokens = sum(r.total_tokens for r in rows)
    dau_sum = sum(r.dau for r in rows)
    payers = last.active_subscriptions
    conversion = payers / last.total_users * 100 if last.total_users else 0
    return (
        f"За {days} дн. (дней со статистикой: {len(rows)}):\n"
        f"- Пользователей: {first.total_users} -> {last.total_users} (прирост {last.total_users - first.total_users})\n"
        f"- Платных сейчас: {payers} из {last.total_users} (конверсия {conversion:.1f}%), "
        f"trial: {last.trial_users}, истекших: {last.expired_users}, забанено: {last.banned_users}\n"
        f"- DAU на последний день: {last.dau}, MAU: {last.mau}\n"
        f"- Cache rate: {hit / prompt * 100 if prompt else 0:.1f}% (цель >65%)\n"
        f"- Токенов всего: {tokens}, в среднем на активного пользователя в день: "
        f"{tokens / dau_sum if dau_sum else 0:.0f}\n"
        f"- Сообщений игроков: {sum(r.messages_today for r in rows)}\n"
        f"- Успешных платежей: {sum(r.successful_payments for r in rows)}, "
        f"выручка: {sum(r.revenue_rub for r in rows):.0f} руб., "
        f"примерная стоимость AI: ${sum(r.estimated_cost_usd for r in rows):.2f}"
    )


async def run_analysis() -> tuple[str, list[tuple[str, str]]]:
    """Возвращает (текст ответа, список (ключ, значение) для кнопок применения)."""
    async with session_factory() as session:
        await crud.save_snapshot(session, crud.local_today())
        await session.commit()
        rows7 = await crud.analytics_since(session, 7)
        rows30 = await crud.analytics_since(session, 30)
        ai = await crud.get_ai_settings(session)
        template = await crud.get_text(session, "analyze_prompt")
        current = (
            f"Текущие настройки AI: model={ai.model}, temperature={ai.temperature}, "
            f"max_tokens={ai.max_tokens}, max_history_messages={ai.max_history_messages}, "
            f"compression_threshold={ai.compression_threshold}"
        )
        model, temperature, max_tokens = ai.model, ai.temperature, ai.max_tokens

    stats = f"{summarize_period(rows7, 7)}\n\n{summarize_period(rows30, 30)}\n\n{current}"
    prompt = template.replace("{stats}", stats) if "{stats}" in template else f"{template}\n{stats}"
    result = await deepseek.chat(
        [{"role": "user", "content": prompt}],
        model=model, temperature=temperature, max_tokens=max_tokens,
    )
    suggestions: dict[str, str] = {}
    for key, value in _SETTING_RE.findall(result.content):
        if key.lower() in AI_PARAMS:
            suggestions[key.lower()] = value
    text = _SETTING_RE.sub("", result.content).strip()
    return f"📊 Данные:\n{stats}\n\n🧠 Рекомендации:\n{text}", list(suggestions.items())


async def _send_analysis(message: Message) -> None:
    wait = await message.answer("📈 Собираю статистику и спрашиваю DeepSeek…")
    try:
        text, suggestions = await run_analysis()
    except DeepSeekError as e:
        await wait.edit_text(f"⚠️ DeepSeek недоступен: {esc(str(e))[:200]}")
        return
    except Exception:
        log.exception("Ошибка анализа")
        await wait.edit_text("⚠️ Не удалось построить анализ, подробности в логах.")
        return

    await wait.delete()
    chunks = [text[i : i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)] or [text]
    for i, chunk in enumerate(chunks):
        markup = None
        if i == len(chunks) - 1:
            buttons = [
                [InlineKeyboardButton(text=f"Применить {k}={v}", callback_data=f"an:set:{k}:{v}"[:64])]
                for k, v in suggestions
            ]
            buttons.append([InlineKeyboardButton(text="◀️ В меню", callback_data="menu:main")])
            markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(esc(chunk), reply_markup=markup)


@router.message(Command("analyze"))
async def cmd_analyze(message: Message) -> None:
    await _send_analysis(message)


@router.callback_query(F.data == "menu:analyze")
async def menu_analyze(cb: CallbackQuery) -> None:
    await cb.answer()
    await _send_analysis(cb.message)


@router.callback_query(F.data.startswith("an:set:"))
async def apply_setting(cb: CallbackQuery) -> None:
    _, _, key, value = cb.data.split(":", 3)
    async with session_factory() as session:
        try:
            applied = await apply_ai_setting(session, key, value)
        except SettingError as e:
            await cb.answer(str(e), show_alert=True)
            return
    await cb.answer(f"✅ {key} = {applied}", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=kb([("◀️ В меню", "menu:main")]))
