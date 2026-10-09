from __future__ import annotations

import logging

from bot.db import DB
from bot.deepseek import DeepSeek
from bot.prompts import language_name
from bot.strings import EN, RU

log = logging.getLogger(__name__)


def norm_lang(code: str | None) -> str:
    if not code:
        return "ru"
    primary = code.replace("_", "-").split("-")[0].lower()
    if not primary.isalpha() or not 2 <= len(primary) <= 8:
        return "ru"
    return primary


class I18n:
    def __init__(self, db: DB, ai: DeepSeek):
        self.db = db
        self.ai = ai

    async def t(self, language: str, key: str, **values) -> str:
        language = norm_lang(language)
        template = await self._template(language, key)
        if not values:
            return template
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            return template

    async def _template(self, language: str, key: str) -> str:
        if language == "ru":
            return RU[key]
        if language == "en":
            return EN[key]
        cached = self.db.translation(language, key)
        if cached:
            return cached
        await self.ensure(language)
        return self.db.translation(language, key) or EN[key]

    async def ensure(self, language: str) -> None:
        missing = [key for key in EN if self.db.translation(language, key) is None]
        if not missing:
            return
        subset = {key: EN[key] for key in missing}
        try:
            translated = await self.ai.translate(language_name(language), subset)
        except Exception:
            log.exception("button translation failed for %s", language)
            translated = {}
        for key in missing:
            value = translated.get(key)
            if not isinstance(value, str) or not value.strip():
                value = EN[key]
            self.db.save_translation(language, key, value)
