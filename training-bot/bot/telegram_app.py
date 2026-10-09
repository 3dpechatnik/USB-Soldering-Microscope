from __future__ import annotations

import asyncio
import html
import logging
import re
import time

import httpx

from bot.config import load_settings
from bot.db import DB
from bot.deepseek import DeepSeek
from bot.logic import format_clock, tick_delay
from bot.service import Screen, Service

log = logging.getLogger(__name__)
TIMERS: dict[int, asyncio.Task] = {}


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.url = f"https://api.telegram.org/bot{token}"
        self.client = client

    async def call(self, method: str, request_timeout: httpx.Timeout | None = None, **payload):
        kwargs = {"json": payload}
        if request_timeout is not None:
            kwargs["timeout"] = request_timeout
        response = await self.client.post(f"{self.url}/{method}", **kwargs)
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


def clock_text(screen: Screen, left: int, step: int, running: bool) -> str:
    total = screen.timer_after or left
    lines = [format_clock(left, total, step, running)]
    if screen.timer_label:
        lines.append(html.escape(screen.timer_label, quote=False))
    if left == 0 and screen.timer_done_text:
        lines.append(html.escape(screen.timer_done_text, quote=False))
    return "\n".join(lines)


def start_timer(tg: Telegram, chat_id: int, screen: Screen, message_id: int) -> None:
    cancel_timer(chat_id)
    total = int(screen.timer_after or 0)

    async def edit(text: str) -> None:
        try:
            await tg.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
            )
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if "not modified" in lowered:
                return
            if "retry after" in lowered:
                match = re.search(r"(\d+)", message)
                await asyncio.sleep(int(match.group(1)) + 1 if match else 5)
                return
            plain = text.replace("<b>", "").replace("</b>", "")
            await tg.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=plain,
            )

    async def run() -> None:
        started = time.monotonic()
        step = 0
        try:
            while True:
                left = max(0, total - int(time.monotonic() - started))
                await edit(clock_text(screen, left, step, running=left > 0))
                if left == 0:
                    if screen.timer_done_text:
                        await tg.call(
                            "sendMessage",
                            chat_id=chat_id,
                            text=screen.timer_done_text,
                            reply_markup=markup(screen),
                        )
                    return
                step += 1
                await asyncio.sleep(tick_delay(left))
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("timer send failed")

    TIMERS[chat_id] = asyncio.create_task(run())


def _plain(text: str) -> str:
    return (
        text.replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
    )


def _pieces(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces = []
    rest = text
    while rest:
        if len(rest) <= limit:
            pieces.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return pieces


async def _send(tg: Telegram, chat_id: int, text: str, board: dict | None) -> dict | None:
    payload: dict = {"chat_id": chat_id, "text": text[:4000]}
    if board:
        payload["reply_markup"] = board
    try:
        payload["parse_mode"] = "HTML"
        result = await tg.call("sendMessage", **payload)
    except Exception:
        log.exception("html send failed")
        payload.pop("parse_mode", None)
        payload["text"] = _plain(text)[:4000]
        result = await tg.call("sendMessage", **payload)
    return result if isinstance(result, dict) else None


async def show(tg: Telegram, chat_id: int, screen: Screen) -> int | None:
    board = markup(screen)
    if screen.timer_after:
        if not screen.timer_only and screen.timer_body:
            for piece in _pieces(screen.timer_body):
                await _send(tg, chat_id, piece, None)
        sent = await _send(tg, chat_id, clock_text(screen, screen.timer_after, 0, True), board)
        if not sent:
            return None
        return sent.get("message_id")
    pieces = _pieces(screen.text)
    for index, piece in enumerate(pieces):
        await _send(tg, chat_id, piece, board if index == len(pieces) - 1 else None)
    return None


async def main() -> None:
    settings = load_settings()
    db = DB(settings.db_path)
    ai = DeepSeek(settings.deepseek_api_key)
    service = Service(db, ai, settings.admin_id)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=12.0, write=10.0, pool=5.0),
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0),
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
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
            screen = await asyncio.wait_for(
                service.on_message(
                    user["id"],
                    user.get("username") or "",
                    full_name,
                    user.get("language_code") or "",
                    text,
                    on_wait=on_wait,
                ),
                timeout=100,
            )
        except TimeoutError:
            log.warning("update timed out")
            await tg.call("sendMessage", chat_id=chat_id, text="Долго нет ответа. Нажми ещё раз.")
            return
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
        try:
            message_id = await show(tg, chat_id, screen)
        except Exception:
            log.exception("show failed")
            await tg.call("sendMessage", chat_id=chat_id, text="Не вышло отправить экран. Нажми ещё раз.")
            return
        if screen.timer_after and message_id:
            start_timer(tg, chat_id, screen, message_id)

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
        workers: set[asyncio.Task] = set()

        async def handle(update: dict) -> None:
            try:
                if update.get("message"):
                    await on_message(update["message"])
                elif update.get("callback_query"):
                    await on_callback(update["callback_query"])
            except Exception:
                log.exception("message failed")

        while True:
            try:
                updates = await tg.call(
                    "getUpdates",
                    request_timeout=httpx.Timeout(connect=5.0, read=14.0, write=10.0, pool=5.0),
                    offset=offset,
                    timeout=8,
                    allowed_updates=["message", "callback_query"],
                )
            except Exception as exc:
                log.warning("poll failed: %s %r", type(exc).__name__, exc)
                await asyncio.sleep(1)
                continue
            for update in updates or []:
                offset = update["update_id"] + 1
                task = asyncio.create_task(handle(update))
                workers.add(task)
                task.add_done_callback(workers.discard)
    finally:
        await ai.close()
        await client.aclose()
