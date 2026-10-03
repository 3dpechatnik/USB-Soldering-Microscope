import asyncio
import logging

from sqlalchemy import update

from app.ai import deepseek
from app.config import settings
from app.db import crud
from app.db.engine import session_factory
from app.db.models import MessageHistory, StorySummary

log = logging.getLogger(__name__)

_running: set[int] = set()
_tasks: set[asyncio.Task] = set()


def schedule_compression(user_id: int) -> None:
    """Запускает сжатие в фоне, чтобы не задерживать ответ игроку."""
    task = asyncio.create_task(maybe_compress(user_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def maybe_compress(user_id: int) -> bool:
    if user_id in _running:
        return False
    _running.add(user_id)
    try:
        return await _compress(user_id)
    except Exception:
        log.exception("Сжатие истории user_id=%s не удалось", user_id)
        return False
    finally:
        _running.discard(user_id)


async def _compress(user_id: int) -> bool:
    async with session_factory() as session:
        ai = await crud.get_ai_settings(session)
        if await crud.count_history(session, user_id) < ai.compression_threshold:
            return False
        history = await crud.get_history(session, user_id, 10_000)
        previous = await crud.latest_summary(session, user_id)
        instruction = await crud.get_text(session, "compression_prompt")
        model = ai.model

    lines = []
    if previous:
        lines.append(f"Previous summary: {previous.summary}")
    for h in history:
        who = "Player" if h.role == "user" else "DM"
        lines.append(f"{who}: {h.content}")

    result = await deepseek.chat(
        [
            {"role": "system", "content": instruction},
            {"role": "user", "content": "\n\n".join(lines)},
        ],
        model=model,
        temperature=settings.COMPRESSION_TEMPERATURE,
        max_tokens=settings.COMPRESSION_MAX_TOKENS,
    )
    if not result.content:
        return False

    keep = settings.COMPRESSION_KEEP_LAST
    to_archive = [h.id for h in history[:-keep]] if keep > 0 else [h.id for h in history]
    async with session_factory() as session:
        session.add(
            StorySummary(user_id=user_id, summary=result.content, messages_covered=len(to_archive))
        )
        if to_archive:
            await session.execute(
                update(MessageHistory)
                .where(MessageHistory.id.in_(to_archive))
                .values(archived=True, content="")
            )
        await session.commit()
    log.info("История user_id=%s сжата: заархивировано %s сообщений", user_id, len(to_archive))
    return True
