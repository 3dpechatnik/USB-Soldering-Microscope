"""Пошаговое создание персонажа: код сам определяет шаг и запоминает выбор игрока,
поэтому в запрос попадает только инструкция текущего шага."""
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import crud
from app.db.models import Character

# Шаги 1..4 задают вопрос игроку, шаг 5 — финал без вопросов (характеристики, снаряжение, finalize).
PICK_KEYS = {1: "era", 2: "name", 3: "class", 4: "background"}
FINAL_STEP = 5
MAX_ANSWER_LEN = 40

_NUMBERED_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(.+?)\s*$")
_SPLIT_RE = re.compile(r"\s+[—–-]\s+|:\s+|\s+\(")


def parse_numbered(text: str) -> dict[int, str]:
    found: dict[int, str] = {}
    for line in text.splitlines():
        m = _NUMBERED_RE.match(line)
        if m:
            found[int(m.group(1))] = m.group(2)
    return found


def short_name(option: str) -> str:
    return _SPLIT_RE.split(option, maxsplit=1)[0].strip(" .*")


def resolve_answer(step: int, text: str, last_assistant: str) -> str | None:
    """Возвращает выбранное значение или None, если ответ не похож на ответ на вопрос шага."""
    text = text.strip()
    if not text or text.startswith("/") or "?" in text or len(text) > MAX_ANSWER_LEN:
        return None
    if step == 2:
        return text
    options = {n: short_name(v) for n, v in parse_numbered(last_assistant).items()}
    if not options:
        return None
    if text.isdigit():
        return options.get(int(text))
    low = text.lower()
    for name in options.values():
        if low == name.lower():
            return name
    if len(low) >= 3:
        for name in options.values():
            if name.lower().startswith(low) or low in name.lower():
                return name
    return None


async def advance(session: AsyncSession, user_id: int, ch: Character, text: str) -> None:
    """Вызывается ДО запроса к ИИ: фиксирует ответ игрока и выбирает шаг для запроса."""
    if ch.is_created:
        return
    step = ch.creation_step
    if step == 0:
        ch.creation_step = 1
        return
    if step in PICK_KEYS:
        last = await crud.last_assistant_message(session, user_id) or ""
        value = resolve_answer(step, text, last)
        if value:
            ch.creation_data = {**(ch.creation_data or {}), PICK_KEYS[step]: value}
            ch.creation_step = step + 1


def picks_line(ch: Character) -> str:
    data = ch.creation_data or {}
    return "|".join(f"{k}: {data[k]}" for k in PICK_KEYS.values() if k in data)


def step_trigger(ch: Character) -> str | None:
    if 1 <= ch.creation_step <= FINAL_STEP:
        return f"creation_step_{ch.creation_step}"
    return None


def history_limit(default: int) -> int:
    return min(default, settings.CREATION_HISTORY)
