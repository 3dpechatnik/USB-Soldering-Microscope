from __future__ import annotations

import logging

import httpx

from bot.logic import parse_model_json, validate_session

log = logging.getLogger(__name__)

URL = "https://api.deepseek.com/chat/completions"
SESSION_MODEL = "deepseek-v4-pro"
TRANSLATE_MODEL = "deepseek-flash"


class DeepSeek:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=20.0, pool=10.0),
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        model: str,
    ) -> str:
        response = await self.client.post(
            URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        if response.status_code >= 400:
            log.error("deepseek status %s", response.status_code)
            response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]

    async def _cards(self, system: str, user: str) -> dict:
        raw = await self.complete(
            system, user, temperature=0.8, max_tokens=2200, model=SESSION_MODEL
        )
        try:
            return validate_session(parse_model_json(raw))
        except Exception:
            log.warning("session json failed, retrying once")
            raw = await self.complete(
                system,
                user + "\n\nThe previous reply was not valid. Return only the JSON object.",
                temperature=0.4,
                max_tokens=2200,
                model=SESSION_MODEL,
            )
            return validate_session(parse_model_json(raw))

    async def session(self, system: str, user: str) -> dict:
        return await self._cards(system, user)

    async def translate(self, language_name: str, mapping: dict[str, str]) -> dict[str, str]:
        import json

        system = (
            "Translate the JSON values into the target language. "
            "Keep every emoji unchanged. "
            "If a value is only a line of underscores and slashes, copy it unchanged. "
            "Return only a JSON object with the same keys."
        )
        user = f"target_language: {language_name}\n{json.dumps(mapping, ensure_ascii=False)}"
        raw = await self.complete(
            system, user, temperature=0.2, max_tokens=4000, model=TRANSLATE_MODEL
        )
        data = parse_model_json(raw)
        return {key: value for key, value in data.items() if isinstance(value, str)}
