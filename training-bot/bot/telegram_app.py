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


# Source address on the server prefix 2a04:bac0:1000:4f8::/64.
# Used only when Telegram over IPv4 does not connect.
TELEGRAM_IPV6_SOURCE = "2a04:bac0:1000:4f8::1"
_V4_HOLD = 180.0


class _BoundIPv6Stream(httpcore.AsyncNetworkStream):
    """TCP stream bound with an IPv6 4-tuple.

    anyio turns that address into a 2-tuple, and asyncio then waits until the
    connect timeout instead of using the address that is already on the host.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.read(max_bytes), timeout)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout(str(exc)) from exc
        except Exception as exc:
            raise httpcore.ReadError(str(exc)) from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        self._writer.write(buffer)
        try:
            await asyncio.wait_for(self._writer.drain(), timeout)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout(str(exc)) from exc
        except Exception as exc:
            raise httpcore.WriteError(str(exc)) from exc

    async def aclose(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            return

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            await asyncio.wait_for(
                self._writer.start_tls(
                    ssl_context,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=timeout,
                ),
                timeout,
            )
        except TimeoutError as exc:
            await self.aclose()
            raise httpcore.ConnectTimeout(str(exc)) from exc
        except Exception as exc:
            await self.aclose()
            raise httpcore.ConnectError(str(exc)) from exc
        return self

    def get_extra_info(self, info: str):
        if info == "ssl_object":
            return self._writer.get_extra_info("ssl_object")
        if info == "client_addr":
            return self._writer.get_extra_info("sockname")
        if info == "server_addr":
            return self._writer.get_extra_info("peername")
        if info == "socket":
            return self._writer.get_extra_info("socket")
        return None


class TelegramIPv4(httpcore.AsyncNetworkBackend):
    """Dial Telegram by IPv4. If that family is down, use the server IPv6 address."""

    def __init__(self, ipv6_source: str = TELEGRAM_IPV6_SOURCE) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._v4: list[str] = []
        self._v6: list[str] = []
        self._v4_index = 0
        self._v6_index = 0
        self._refreshed = 0.0
        self._v4_down_until = 0.0
        self._on_v6 = False
        self._ipv6_source = ipv6_source

    def prefer_ipv6(self) -> None:
        """Stay on IPv6 after a dead IPv4 path, including a path that accepts TCP and then hangs."""
        if not self._v6:
            return
        self._v4_down_until = time.monotonic() + _V4_HOLD
        if not self._on_v6:
            log.warning("telegram ipv4 down, backup ipv6 %s", self._ipv6_source)
            self._on_v6 = True

    async def _resolve(self, family: int) -> list[str] | None:
        loop = asyncio.get_running_loop()
        try:
            found = await asyncio.wait_for(
                loop.getaddrinfo(
                    "api.telegram.org",
                    443,
                    family=family,
                    type=socket.SOCK_STREAM,
                ),
                timeout=2,
            )
        except Exception:
            return None
        ips: list[str] = []
        for item in found:
            ip = item[4][0]
            if ip not in ips:
                ips.append(ip)
        return ips

    async def _load_ips(self) -> None:
        now = time.monotonic()
        if (self._v4 or self._v6) and now - self._refreshed < 600:
            return
        v4 = await self._resolve(socket.AF_INET)
        v6 = await self._resolve(socket.AF_INET6)
        if v4 is not None:
            self._v4 = v4
            if v4:
                self._v4_index %= len(v4)
        if v6 is not None:
            self._v6 = v6
            if v6:
                self._v6_index %= len(v6)
        if v4 is not None or v6 is not None:
            self._refreshed = now

    async def _open(self, host, port, timeout, local_address, socket_options):
        if ":" in host:
            return await self._connect_bound_ipv6(host, port, timeout, local_address, socket_options)
        return await self._inner.connect_tcp(host, port, timeout, local_address, socket_options)

    async def _connect_bound_ipv6(self, ip, port, timeout, local, socket_options):
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if socket_options:
                for option in socket_options:
                    sock.setsockopt(*option)
            sock.setblocking(False)
            # 4-tuple: a 2-tuple local address makes asyncio wait out the timeout.
            sock.bind((local, 0, 0, 0))
            await asyncio.wait_for(loop.sock_connect(sock, (ip, int(port), 0, 0)), timeout)
            reader, writer = await asyncio.open_connection(sock=sock)
            sock = None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        return _BoundIPv6Stream(reader, writer)

    async def _dial(self, ips: list[str], index_name: str, local: str, port, each, socket_options, attempts: int):
        last: Exception | None = None
        for _ in range(min(attempts, len(ips))):
            index = getattr(self, index_name) % len(ips)
            ip = ips[index]
            try:
                stream = await self._open(ip, port, each, local, socket_options)
            except Exception as exc:
                last = exc
                setattr(self, index_name, index + 1)
                continue
            return stream
        assert last is not None
        raise last

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        local = local_address or "0.0.0.0"
        if host != "api.telegram.org":
            return await self._inner.connect_tcp(host, port, timeout, local, socket_options)
        await self._load_ips()
        if not self._v4 and not self._v6:
            return await self._inner.connect_tcp(host, port, timeout, local, socket_options)
        each = 4.0 if timeout is None else min(4.0, float(timeout))
        # Leave room for the IPv6 backup inside the 15s send budget.
        v4_each = min(each, 2.0) if self._v6 else each
        v4_attempts = 2 if self._v6 else 3
        now = time.monotonic()
        # A dead IPv4 route can still accept the TCP handshake and then hang.
        # While the hold lasts, do not touch it.
        v4_first = now >= self._v4_down_until or not self._v6
        last: Exception | None = None
        if v4_first and self._v4:
            try:
                stream = await self._dial(
                    self._v4, "_v4_index", "0.0.0.0", port, v4_each, socket_options, v4_attempts
                )
            except Exception as exc:
                last = exc
            else:
                self._on_v6 = False
                return stream
        if self._v6:
            try:
                stream = await self._dial(
                    self._v6, "_v6_index", self._ipv6_source, port, each, socket_options, 2
                )
            except Exception as exc:
                last = exc
            else:
                self.prefer_ipv6()
                return stream
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
    def __init__(
        self,
        token: str,
        send: httpx.AsyncClient,
        poll: httpx.AsyncClient,
        route: TelegramIPv4 | None = None,
    ):
        self.url = f"https://api.telegram.org/bot{token}"
        self.send = send
        self.poll = poll
        self.route = route
        self.gate = asyncio.Semaphore(20)

    def _lost(self, exc: BaseException) -> None:
        if self.route is None:
            return
        if isinstance(
            exc,
            (
                TimeoutError,
                httpx.TransportError,
                httpcore.ConnectError,
                httpcore.ConnectTimeout,
                httpcore.ReadError,
                httpcore.ReadTimeout,
                httpcore.WriteError,
                httpcore.WriteTimeout,
                httpcore.NetworkError,
                httpcore.ProtocolError,
            ),
        ):
            self.route.prefer_ipv6()

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
            try:
                return await asyncio.wait_for(
                    self._post(self.send, method, request_timeout, payload),
                    timeout=15,
                )
            except Exception as exc:
                self._lost(exc)
                if isinstance(exc, TimeoutError):
                    log.warning("telegram %s timed out", method)
                raise

    async def long_poll(self, **payload):
        try:
            return await asyncio.wait_for(
                self._post(
                    self.poll,
                    "getUpdates",
                    httpx.Timeout(connect=4.0, read=8.0, write=8.0, pool=4.0),
                    payload,
                ),
                timeout=12,
            )
        except Exception as exc:
            self._lost(exc)
            raise


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
        "is_persistent": False,
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
    tg = Telegram(settings.bot_token, send, poll, backend)

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
