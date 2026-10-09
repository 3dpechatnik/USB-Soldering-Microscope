from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    deepseek_api_key: str
    admin_id: int
    db_path: str


def load_settings() -> Settings:
    token = os.environ.get("BOT_TOKEN", "").strip()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    admin = os.environ.get("ADMIN_ID", "").strip()
    db_path = os.environ.get("DB_PATH", "data/bot.db").strip()
    if not token or not key or not admin:
        raise RuntimeError("BOT_TOKEN, DEEPSEEK_API_KEY and ADMIN_ID are required")
    return Settings(
        bot_token=token,
        deepseek_api_key=key,
        admin_id=int(admin),
        db_path=db_path,
    )
