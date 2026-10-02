import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class DeepSeekError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    parse_error: bool = False


@dataclass
class AIResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    raw_message: dict | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add_usage(self, other: "AIResult") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cache_hit_tokens += other.cache_hit_tokens


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(settings.DEEPSEEK_TIMEOUT, connect=15.0),
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
        )
    return _client


async def close_client() -> None:
    if _client is not None and not _client.is_closed:
        await _client.aclose()


def _parse_tool_calls(message: dict) -> list[ToolCall]:
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError("arguments не объект")
            err = False
        except (ValueError, TypeError):
            log.warning("Не удалось разобрать аргументы tool call: %r", raw[:300])
            args, err = {}, True
        calls.append(
            ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args, parse_error=err)
        )
    return calls


async def chat(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
) -> AIResult:
    """Один запрос к DeepSeek с повторами. Бросает DeepSeekError после всех неудач."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

    last_error: Exception | None = None
    for attempt in range(1, settings.DEEPSEEK_RETRIES + 1):
        try:
            resp = await _get_client().post("/chat/completions", json=payload)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise DeepSeekError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                log.error("DeepSeek отклонил запрос: %s %s", resp.status_code, resp.text[:500])
                raise DeepSeekError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
            usage = data.get("usage") or {}
            return AIResult(
                content=(message.get("content") or "").strip(),
                tool_calls=_parse_tool_calls(message),
                finish_reason=choice.get("finish_reason"),
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0),
                raw_message=message,
            )
        except (httpx.HTTPError, DeepSeekError, KeyError, IndexError, ValueError) as e:
            last_error = e
            log.warning("DeepSeek: попытка %s/%s не удалась: %s", attempt, settings.DEEPSEEK_RETRIES, e)
            if isinstance(e, DeepSeekError) and "HTTP 4" in str(e) and "HTTP 429" not in str(e):
                break
            if attempt < settings.DEEPSEEK_RETRIES:
                await asyncio.sleep(settings.DEEPSEEK_RETRY_DELAY)
    raise DeepSeekError(str(last_error))
