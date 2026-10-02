"""Стартовые данные. Идемпотентно: существующие (уже отредактированные) записи не трогаем."""
import asyncio
import logging
import re

from sqlalchemy import select

from app.config import BASE_DIR, settings
from app.db.engine import engine, session_factory
from app.db.models import AISettings, PromptModule

log = logging.getLogger(__name__)

GAME_PROMPT_FILE = BASE_DIR / "game_prompt.txt"
MODULE_FILES = {"base": GAME_PROMPT_FILE, "character_creation": BASE_DIR / "character_creation.txt"}
CREATION_STEPS_FILE = BASE_DIR / "creation_steps.txt"

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
        "[Reminder: if anything changed this turn, call update_state with the reply. "
        "Never act or invent items for the player.]"
    ),
    "compression_prompt": (
        "Compress this D&D RPG dialogue (merge with any previous summary) into a summary of at most "
        "200 words, in Russian. Keep events, NPCs, items, quests, promises, places."
    ),
    "analyze_prompt": (
        "RPG bot stats for 7 and 30 days:\n{stats}\n\n"
        "Give 3-5 concrete recommendations (prompt, AI settings, game balance, conversion). "
        "Format: problem -> recommendation -> expected effect. Answer in Russian. "
        "For a numeric AI-setting recommendation add, on its own line at the end, a marker like "
        "[SETTING temperature=0.6]. Allowed keys: temperature, max_tokens, max_history_messages, "
        "compression_threshold; at most one marker per key."
    ),
}

DEFAULT_MODULES: list[tuple[str, str, int]] = [
    ("base", "always", 0),
    ("character_creation", "character_creation", 10),
    ("combat", "combat", 20),
    ("rest", "rest", 30),
    ("trading", "trading", 40),
]
CREATION_STEP_MODULES = [(f"creation_step_{n}", f"creation_step_{n}", 10 + n) for n in range(1, 6)]


def read_module_file(name: str) -> str:
    path = MODULE_FILES.get(name)
    if path is None:
        return ""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    log.warning("%s не найден — модуль %s будет пустым", path.name, name)
    return ""


def read_creation_steps() -> dict[str, str]:
    """creation_steps.txt: блоки '=== creation_step_N ===' -> {имя модуля: текст}."""
    if not CREATION_STEPS_FILE.exists():
        log.warning("creation_steps.txt не найден — шаги создания персонажа будут пустыми")
        return {}
    parts = re.split(r"^=== (\w+) ===\s*$", CREATION_STEPS_FILE.read_text(encoding="utf-8"), flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def read_game_prompt() -> str:
    return read_module_file("base")


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
        steps = read_creation_steps()
        for name, trigger, order in DEFAULT_MODULES + CREATION_STEP_MODULES:
            if name in existing:
                continue
            content = steps.get(name) if name in steps else read_module_file(name)
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
