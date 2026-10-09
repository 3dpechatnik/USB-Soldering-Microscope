from __future__ import annotations

import asyncio
import html
import logging
import re
import socket
import time

import httpcore
import httpx
from httpx._config import create_ssl_context

from bot.config import load_settings
from bot.db import DB
from bot.deepseek import DeepSeek
from bot.logic import format_clock, tick_delay
from bot.service import Screen, Service

log = logging.getLogger(__name__)
TIMERS: dict[int, asyncio.Task] = {}
MENU_CHATS: set[int] = set()
MENU_RU = [
    {"command": "review", "description": "Оставить отзыв"},
    {"command": "subscribe", "description": "Подписка"},
    {"command": "delete", "description": "Удалить данные"},
]
MENU_EN = [
    {"command": "review", "description": "Leave a review"},
    {"command": "subscribe", "description": "Subscription"},
    {"command": "delete", "description": "Delete data"},
]


class TelegramIPv4(httpcore.AsyncNetworkBackend):
    """Dial Telegram by IPv4 and move to the next address when one does not connect."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._ips: list[str] = []
        self._index = 0
        self._refreshed = 0.0

    def _ensure_ips(self) -> None:
        now = time.monotonic()
        if self._ips and now - self._refreshed < 600:
            return
        try:
            found = socket.getaddrinfo(
                "api.telegram.org", 443, socket.AF_INET, socket.SOCK_STREAM
            )
        except socket.gaierror:
            return
        ips: list[str] = []
        for item in found:
            ip = item[4][0]
            if ip not in ips:
                ips.append(ip)
        if ips:
            self._ips = ips
            self._index %= len(ips)
            self._refreshed = now

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        local = local_address or "0.0.0.0"
        if host != "api.telegram.org":
            return await self._inner.connect_tcp(host, port, timeout, local, socket_options)
        self._ensure_ips()
        if not self._ips:
            return await self._inner.connect_tcp(host, port, timeout, local, socket_options)
        last: Exception | None = None
        each = 4.0 if timeout is None else min(4.0, float(timeout))
        for _ in range(min(3, len(self._ips))):
            ip = self._ips[self._index % len(self._ips)]
            try:
                return await self._inner.connect_tcp(ip, port, each, local, socket_options)
            except Exception as exc:
                last = exc
                self._index += 1
        assert last is not None
        raise last

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._inner.connect_unix_socket(path, timeout, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def open_client(backend: TelegramIPv4, connections: int, timeout: httpx.Timeout) -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=connections,
        max_keepalive_connections=0,
        keepalive_expiry=1.0,
    )
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0, limits=limits)
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=create_ssl_context(verify=True, cert=None, trust_env=True),
        max_connections=connections,
        max_keepalive_connections=0,
        keepalive_expiry=1.0,
        local_address="0.0.0.0",
        retries=0,
        network_backend=backend,
    )
    return httpx.AsyncClient(timeout=timeout, transport=transport, limits=limits)


PROTECTED_METHODS = frozenset(
    {
        "sendMessage",
        "sendPhoto",
        "sendAudio",
        "sendDocument",
        "sendVideo",
        "sendAnimation",
        "sendVoice",
        "sendVideoNote",
        "sendSticker",
        "sendMediaGroup",
        "copyMessage",
        "sendPoll",
        "sendDice",
        "sendLocation",
        "sendVenue",
        "sendContact",
        "sendInvoice",
        "sendGame",
        "sendPaidMedia",
    }
)


class Telegram:
    def __init__(self, token: str, send: httpx.AsyncClient, poll: httpx.AsyncClient):
        self.url = f"https://api.telegram.org/bot{token}"
        self.send = send
        self.poll = poll
        self.gate = asyncio.Semaphore(20)

    async def _post(self, client: httpx.AsyncClient, method: str, request_timeout, payload: dict):
        kwargs = {"json": payload}
        if request_timeout is not None:
            kwargs["timeout"] = request_timeout
        response = await client.post(f"{self.url}/{method}", **kwargs)
        data = response.json()
        if not data.get("ok"):
            description = data.get("description", "telegram error")
            raise RuntimeError(description)
        return data.get("result")

    async def call(self, method: str, request_timeout: httpx.Timeout | None = None, **payload):
        if method in PROTECTED_METHODS:
            payload["protect_content"] = True
        async with self.gate:
            return await self._post(self.send, method, request_timeout, payload)

    async def long_poll(self, **payload):
        return await self._post(
            self.poll,
            "getUpdates",
            httpx.Timeout(connect=4.0, read=8.0, write=8.0, pool=4.0),
            payload,
        )


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


def live_text(screen: Screen, left: int, step: int, running: bool) -> str:
    clock = clock_text(screen, left, step, running)
    if screen.timer_body and not screen.timer_only:
        return f"{screen.timer_body}\n\n{clock}"
    return clock


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
                await edit(live_text(screen, left, step, running=left > 0))
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
        text = live_text(screen, screen.timer_after, 0, True)
    else:
        text = screen.text
    pieces = _pieces(text)
    sent = None
    for index, piece in enumerate(pieces):
        sent = await _send(tg, chat_id, piece, board if index == len(pieces) - 1 else None)
    if screen.timer_after and sent and len(pieces) == 1:
        return sent.get("message_id")
    return None


async def main() -> None:
    settings = load_settings()
    db = DB(settings.db_path)
    ai = DeepSeek(settings.deepseek_api_key)
    service = Service(db, ai, settings.admin_id)
    backend = TelegramIPv4()
    send = open_client(
        backend, 40, httpx.Timeout(connect=5.0, read=12.0, write=10.0, pool=5.0)
    )
    poll = open_client(
        backend, 4, httpx.Timeout(connect=4.0, read=8.0, write=8.0, pool=4.0)
    )
    tg = Telegram(settings.bot_token, send, poll)

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
        if chat_id not in MENU_CHATS:
            MENU_CHATS.add(chat_id)
            try:
                await tg.call(
                    "setChatMenuButton",
                    chat_id=chat_id,
                    menu_button={"type": "commands"},
                )
            except Exception:
                MENU_CHATS.discard(chat_id)
                log.warning("menu button for chat failed")
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
                timeout=150,
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
        for commands, language in ((MENU_RU, None), (MENU_RU, "ru"), (MENU_EN, "en")):
            try:
                payload = {"commands": commands}
                if language:
                    payload["language_code"] = language
                await tg.call("setMyCommands", **payload)
            except Exception as exc:
                log.warning("menu commands failed: %s", exc)
        try:
            await tg.call("setChatMenuButton", menu_button={"type": "commands"})
        except Exception as exc:
            log.warning("menu button failed: %s", exc)
        for tg_id in db.user_ids():
            try:
                await tg.call(
                    "setChatMenuButton",
                    chat_id=tg_id,
                    menu_button={"type": "commands"},
                )
            except Exception as exc:
                log.warning("menu button for chat failed: %s", exc)
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
                updates = await tg.long_poll(
                    offset=offset,
                    timeout=3,
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
        await send.aclose()
        await poll.aclose()
