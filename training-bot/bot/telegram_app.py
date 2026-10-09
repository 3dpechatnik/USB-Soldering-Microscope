from __future__ import annotations

import asyncio
import logging

import httpx

from bot.config import load_settings
from bot.db import DB
from bot.deepseek import DeepSeek
from bot.service import Screen, Service

log = logging.getLogger(__name__)
TIMERS: dict[int, asyncio.Task] = {}


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.url = f"https://api.telegram.org/bot{token}"
        self.client = client

    async def call(self, method: str, **payload):
        response = await self.client.post(f"{self.url}/{method}", json=payload)
        data = response.json()
        if not data.get("ok"):
            description = data.get("description", "telegram error")
            raise RuntimeError(description)
        return data.get("result")


def markup(screen: Screen) -> dict:
    if screen.inline:
        return {
            "inline_keyboard": [
                [{"text": label, "callback_data": action}] for action, label in screen.actions
            ]
        }
    return {
        "keyboard": [[{"text": label}] for _, label in screen.actions],
        "resize_keyboard": False,
        "is_persistent": True,
    }


def cancel_timer(chat_id: int) -> None:
    task = TIMERS.pop(chat_id, None)
    if task and not task.done():
        task.cancel()


def start_timer(tg: Telegram, chat_id: int, screen: Screen) -> None:
    cancel_timer(chat_id)

    async def run() -> None:
        try:
            await asyncio.sleep(screen.timer_after or 0)
            await tg.call(
                "sendMessage",
                chat_id=chat_id,
                text=screen.timer_done_text or "Время.",
                reply_markup=markup(screen),
            )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("timer send failed")

    TIMERS[chat_id] = asyncio.create_task(run())


async def show(tg: Telegram, chat_id: int, screen: Screen) -> None:
    text = screen.text
    if len(text) > 4000:
        text = text[:3990] + "…"
    board = markup(screen)
    try:
        await tg.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=board,
        )
    except Exception:
        log.exception("html send failed")
        plain = (
            text.replace("<b>", "")
            .replace("</b>", "")
            .replace("<i>", "")
            .replace("</i>", "")
        )
        await tg.call("sendMessage", chat_id=chat_id, text=plain, reply_markup=board)


async def main() -> None:
    settings = load_settings()
    db = DB(settings.db_path)
    ai = DeepSeek(settings.deepseek_api_key)
    service = Service(db, ai, settings.admin_id)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=8.0, read=30.0, write=20.0, pool=8.0),
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2),
        limits=httpx.Limits(max_keepalive_connections=0),
    )
    tg = Telegram(settings.bot_token, client)

    async def pulse(chat_id: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await tg.call("sendChatAction", chat_id=chat_id, action="typing")
            except Exception:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=4)
            except TimeoutError:
                continue

    async def on_message(message: dict) -> None:
        if message.get("chat", {}).get("type") != "private":
            return
        text = message.get("text")
        if not text:
            return
        user = message["from"]
        chat_id = message["chat"]["id"]
        log.info("message from %s", user.get("id"))
        cancel_timer(chat_id)
        stop = asyncio.Event()
        typing = asyncio.create_task(pulse(chat_id, stop))
        full_name = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        )

        async def on_wait(wait_text: str) -> None:
            await tg.call("sendMessage", chat_id=chat_id, text=wait_text)

        try:
            screen = await service.on_message(
                user["id"],
                user.get("username") or "",
                full_name,
                user.get("language_code") or "",
                text,
                on_wait=on_wait,
            )
        except Exception:
            log.exception("update failed")
            await tg.call("sendMessage", chat_id=chat_id, text="Не вышло. Нажми /start")
            return
        finally:
            stop.set()
            typing.cancel()
        if screen.admin_text:
            try:
                await tg.call(
                    "sendMessage",
                    chat_id=settings.admin_id,
                    text=screen.admin_text,
                )
            except Exception:
                log.exception("admin notify failed")
        await show(tg, chat_id, screen)
        if screen.timer_after:
            start_timer(tg, chat_id, screen)

    async def on_callback(callback: dict) -> None:
        try:
            await tg.call("answerCallbackQuery", callback_query_id=callback["id"])
        except Exception:
            log.warning("callback answer failed")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        await on_message(
            {
                "chat": chat,
                "from": callback.get("from") or {},
                "text": callback.get("data") or "",
            }
        )

    offset = 0
    try:
        for attempt in range(5):
            try:
                await tg.call("deleteWebhook", drop_pending_updates=False)
                break
            except Exception as exc:
                log.warning("webhook reset failed: %s", exc)
                await asyncio.sleep(2)
        else:
            raise RuntimeError("telegram is unreachable")
        try:
            await tg.call(
                "sendMessage",
                chat_id=settings.admin_id,
                text="Бот тренировок на связи. Откройте его и нажмите /start.",
            )
        except Exception as exc:
            log.warning("admin hello failed: %s", exc)
        log.info("training bot polling")
        while True:
            try:
                updates = await tg.call(
                    "getUpdates",
                    offset=offset,
                    timeout=8,
                    allowed_updates=["message", "callback_query"],
                )
            except Exception as exc:
                log.warning("poll failed: %s %r", type(exc).__name__, exc)
                await asyncio.sleep(2)
                continue
            for update in updates or []:
                offset = update["update_id"] + 1
                try:
                    if update.get("message"):
                        await on_message(update["message"])
                    elif update.get("callback_query"):
                        await on_callback(update["callback_query"])
                except Exception:
                    log.exception("message failed")
    finally:
        await ai.close()
        await client.aclose()
