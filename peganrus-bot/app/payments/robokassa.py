"""Заглушка Robokassa. Реальная интеграция будет подключена позже, интерфейс уже готов."""
import logging

from app.config import settings

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.ROBOKASSA_LOGIN and settings.ROBOKASSA_PASSWORD1 and settings.ROBOKASSA_PASSWORD2)


async def create_invoice(user_id: int, amount: float, recurring: bool = False) -> str | None:
    """Вернёт ссылку на оплату или None, если оплата недоступна."""
    log.info("Robokassa (заглушка): счёт user_id=%s amount=%s recurring=%s", user_id, amount, recurring)
    return None


async def charge_recurring(user_id: int, payment_method_id: str, amount: float) -> bool:
    """Автосписание. Заглушка всегда возвращает False (ничего не списано)."""
    log.info("Robokassa (заглушка): автосписание user_id=%s пропущено", user_id)
    return False
