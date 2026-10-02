from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str
    ADMIN_BOT_TOKEN: str
    ADMIN_TELEGRAM_ID: int = 0

    DATABASE_URL: str
    AUTO_MIGRATE: bool = True

    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_MAX_TOKENS: int = 2000
    DEEPSEEK_TEMPERATURE: float = 0.7
    DEEPSEEK_TIMEOUT: float = 120.0
    DEEPSEEK_RETRIES: int = 3
    DEEPSEEK_RETRY_DELAY: float = 3.0
    DEEPSEEK_PRICE_INPUT_MISS: float = 0.28
    DEEPSEEK_PRICE_INPUT_HIT: float = 0.028
    DEEPSEEK_PRICE_OUTPUT: float = 0.42

    ROBOKASSA_LOGIN: str = ""
    ROBOKASSA_PASSWORD1: str = ""
    ROBOKASSA_PASSWORD2: str = ""

    SUBSCRIPTION_PRICE: int = 400
    SUBSCRIPTION_DAYS: int = 30
    LIMIT_TRIAL: int = 30
    DAILY_LIMIT_PAID: int = 100

    TIMEZONE: str = "Europe/Moscow"
    MAX_MESSAGE_LENGTH: int = 500
    ANTIFLOOD_SECONDS: float = 3.0
    TELEGRAM_CHUNK_LENGTH: int = 2096
    FREE_COMMANDS: str = (
        "start,agree,help,privacy,status,subscribe,subscribe_once,subscribe_auto,"
        "autorenew,delete_account,confirm_delete,character,inventory"
    )
    DELETE_CONFIRM_SECONDS: int = 300
    START_GAME_TEXT: str = "Начать игру"
    CUSTOM_OPTION_HINT: str = "Напиши свой вариант действия одним сообщением."

    DEFAULT_MAX_HISTORY: int = 10
    DEFAULT_COMPRESSION_THRESHOLD: int = 18
    COMPRESSION_KEEP_LAST: int = 5
    COMPRESSION_MAX_TOKENS: int = 500
    COMPRESSION_TEMPERATURE: float = 0.3
    ASSISTANT_PRIMER: str = "Understood. Ready to continue the game."
    TRADING_KEYWORDS: str = (
        "рынок,торговец,торговк,лавка,магазин,кузниц,купец,базар,таверн,трактир,лавочник"
    )
    ENCYCLOPEDIA_CONTEXT_LIMIT: int = 20
    ENCYCLOPEDIA_PINNED: str = "Эпоха"
    XP_THRESHOLDS: str = (
        "0,300,900,2700,6500,14000,23000,34000,48000,64000,"
        "85000,100000,120000,140000,165000,195000,225000,265000,305000,355000"
    )

    SCHEDULER_EXPIRE_AT: str = "00:05"
    SCHEDULER_RENEW_AT: str = "10:00"
    SCHEDULER_REMIND_AT: str = "12:00"
    SCHEDULER_STATS_AT: str = "23:55"
    RENEW_REMIND_DAYS: int = 3

    BROADCAST_DELAY: float = 0.05

    @field_validator("ADMIN_TELEGRAM_ID", mode="before")
    @classmethod
    def _empty_admin_id(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return 0
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.TIMEZONE)

    @property
    def free_commands(self) -> set[str]:
        return {c.strip().lower() for c in self.FREE_COMMANDS.split(",") if c.strip()}

    @property
    def trading_keywords(self) -> list[str]:
        return [k.strip().lower() for k in self.TRADING_KEYWORDS.split(",") if k.strip()]

    @property
    def encyclopedia_pinned(self) -> set[str]:
        return {k.strip() for k in self.ENCYCLOPEDIA_PINNED.split(",") if k.strip()}

    @property
    def xp_thresholds(self) -> list[int]:
        return [int(x) for x in self.XP_THRESHOLDS.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
