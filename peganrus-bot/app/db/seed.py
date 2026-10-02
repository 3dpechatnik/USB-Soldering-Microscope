"""Стартовые данные. Идемпотентно: существующие (уже отредактированные) записи не трогаем."""
import asyncio
import logging

from sqlalchemy import select

from app.config import BASE_DIR, settings
from app.db.engine import engine, session_factory
from app.db.models import AISettings, PromptModule

log = logging.getLogger(__name__)

GAME_PROMPT_FILE = BASE_DIR / "game_prompt.txt"

DEFAULT_TEXTS: dict[str, str] = {
    "text_privacy": (
        "🔞 Бот «Подземелье и Горынычи» — игра для совершеннолетних (18+).\n\n"
        "Что нужно знать:\n"
        "• Игру ведёт искусственный интеллект. Тексты генерируются автоматически и "
        "могут содержать ошибки, мрачные и жестокие сюжеты в рамках фэнтези.\n"
        "• Мы храним: ваш Telegram ID, имя, username, данные персонажа, историю игровых "
        "сообщений и данные об оплате. Тексты ваших сообщений передаются сервису "
        "DeepSeek для генерации ответа.\n"
        "• Данные нужны только для работы игры, не продаются и не передаются третьим лицам, "
        "кроме сервисов, без которых игра не работает (ИИ и платёжная система).\n"
        "• Вы можете в любой момент полностью удалить свои данные командой /delete_account.\n\n"
        "Нажимая «Согласен», вы подтверждаете, что вам исполнилось 18 лет и вы принимаете "
        "эти условия."
    ),
    "text_help": (
        "🐉 «Подземелье и Горынычи» — текстовая D&D-игра с ИИ-Мастером.\n\n"
        "Просто пишите, что делает ваш персонаж, или выбирайте один из четырёх вариантов "
        "под сообщением Мастера.\n\n"
        "Команды:\n"
        "/status — подписка и лимиты\n"
        "/character — лист персонажа\n"
        "/inventory — инвентарь\n"
        "/subscribe — оформить подписку\n"
        "/autorenew — вкл/выкл автопродление\n"
        "/privacy — условия и конфиденциальность\n"
        "/delete_account — удалить аккаунт и все данные\n"
        "/help — эта справка"
    ),
    "text_welcome": (
        "✅ Спасибо! Согласие принято.\n\n"
        "Добро пожаловать в «Подземелье и Горынычи». Сейчас Мастер поможет вам создать "
        "персонажа и начать приключение.\n\n"
        "Нажмите кнопку ниже, чтобы начать."
    ),
    "turn_reminder": (
        "[Напоминание Мастеру: если в этом ходе изменились HP, деньги, предметы, опыт, локация, "
        "заклинания, репутация или бой/отдых — ОБЯЗАТЕЛЬНО вызови update_state вместе с ответом. "
        "Не выполняй действия за игрока и не придумывай ему предметы.]"
    ),
    "compression_prompt": (
        "Сожми диалог D&D RPG в резюме 200 слов. Сохрани события, NPC, предметы, квесты."
    ),
    "analyze_prompt": (
        "Вот статистика RPG-бота за 7 и 30 дней:\n{stats}\n\n"
        "Дай 3-5 конкретных рекомендации по улучшению: промпт, настройки AI, игровой баланс, "
        "конверсия. Формат: проблема → рекомендация → ожидаемый эффект.\n\n"
        "Если рекомендация касается числовой настройки AI, добавь отдельной строкой в конце "
        "ответа маркер вида [SETTING temperature=0.6]. Допустимые ключи: temperature, "
        "max_tokens, max_history_messages, compression_threshold. Не больше одного маркера "
        "на ключ."
    ),
}

DEFAULT_MODULES: list[tuple[str, str, int]] = [
    ("base", "always", 0),
    ("character_creation", "character_creation", 10),
    ("combat", "combat", 20),
    ("rest", "rest", 30),
    ("trading", "trading", 40),
]


def read_game_prompt() -> str:
    if GAME_PROMPT_FILE.exists():
        return GAME_PROMPT_FILE.read_text(encoding="utf-8").strip()
    log.warning("game_prompt.txt не найден — модуль base будет пустым")
    return ""


async def seed() -> None:
    async with session_factory() as session:
        if not await session.get(AISettings, 1):
            session.add(
                AISettings(
                    id=1,
                    model=settings.DEEPSEEK_MODEL,
                    temperature=settings.DEEPSEEK_TEMPERATURE,
                    max_tokens=settings.DEEPSEEK_MAX_TOKENS,
                    max_history_messages=settings.DEFAULT_MAX_HISTORY,
                    compression_threshold=settings.DEFAULT_COMPRESSION_THRESHOLD,
                    system_prompt_version="v1",
                )
            )
        existing = set((await session.scalars(select(PromptModule.name))).all())
        for name, trigger, order in DEFAULT_MODULES:
            if name in existing:
                continue
            content = read_game_prompt() if name == "base" else ""
            session.add(
                PromptModule(
                    name=name, trigger_type=trigger, content=content, sort_order=order
                )
            )
        for name, content in DEFAULT_TEXTS.items():
            if name not in existing:
                session.add(
                    PromptModule(name=name, trigger_type="manual", content=content, sort_order=100)
                )
        await session.commit()


async def _main() -> None:
    await seed()
    await engine.dispose()
    print("Стартовые данные на месте.")


if __name__ == "__main__":
    asyncio.run(_main())
