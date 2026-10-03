import asyncio
import logging
import signal
import sys

from alembic import command
from alembic.config import Config

from app.admin.bot import create_admin_bot, create_admin_dispatcher
from app.ai import deepseek
from app.bot.bot import create_bot, create_dispatcher
from app.config import BASE_DIR, settings
from app.db.engine import engine
from app.db.seed import seed
from app.scheduler.jobs import create_scheduler

log = logging.getLogger("peganrus")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def run_migrations() -> None:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    command.upgrade(cfg, "head")


async def run() -> None:
    if settings.AUTO_MIGRATE:
        await asyncio.to_thread(run_migrations)
        setup_logging()
        log.info("Миграции применены")
    await seed()

    game_bot = create_bot()
    admin_bot = create_admin_bot()
    game_dp = create_dispatcher()
    admin_dp = create_admin_dispatcher(game_bot)
    scheduler = create_scheduler(game_bot)

    game_me, admin_me = await game_bot.get_me(), await admin_bot.get_me()
    log.info("Игровой бот: @%s | админ-бот: @%s", game_me.username, admin_me.username)
    if not settings.ADMIN_TELEGRAM_ID:
        log.warning("ADMIN_TELEGRAM_ID не задан: админ-бот ответит всем только их Telegram ID")

    # Если раньше был вебхук, polling не заработает — снимаем.
    await game_bot.delete_webhook(drop_pending_updates=False)
    await admin_bot.delete_webhook(drop_pending_updates=False)

    scheduler.start()
    tasks = [
        asyncio.create_task(game_dp.start_polling(game_bot, handle_signals=False), name="game-polling"),
        asyncio.create_task(admin_dp.start_polling(admin_bot, handle_signals=False), name="admin-polling"),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

    log.info("Оба бота запущены (polling). Остановка: Ctrl+C")
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait([stopper, *tasks], return_when=asyncio.FIRST_COMPLETED)
    finally:
        log.info("Останавливаюсь…")
        scheduler.shutdown(wait=False)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await game_bot.session.close()
        await admin_bot.session.close()
        await deepseek.close_client()
        await engine.dispose()

    for task in tasks:
        if task.done() and not task.cancelled() and task.exception():
            raise task.exception()


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
