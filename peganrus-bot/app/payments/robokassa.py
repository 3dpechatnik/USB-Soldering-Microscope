"""Одноразовая оплата через Robokassa.

Домен не нужен: ссылка на оплату открывается у Робокассы, а бот сам спрашивает
статус счёта (OpStateExt), когда игрок нажимает «Я оплатил».
Автоматических списаний нет.
"""
import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
STATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"
_NS = {"m": "http://merchant.roboxchange.com/WebService/"}

# 100 — операция успешно завершена. 50 — деньги получены, зачисление ещё идёт.
PAID_STATES = {100}
PENDING_STATES = {5, 50}


def is_configured() -> bool:
    return bool(settings.ROBOKASSA_LOGIN and settings.ROBOKASSA_PASSWORD1 and settings.ROBOKASSA_PASSWORD2)


def out_sum(amount: float) -> str:
    return f"{amount:.2f}"


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def payment_signature(inv_id: int, amount: float) -> str:
    raw = f"{settings.ROBOKASSA_LOGIN}:{out_sum(amount)}:{inv_id}:{settings.ROBOKASSA_PASSWORD1}"
    return _md5(raw)


def build_payment_url(inv_id: int, amount: float) -> str | None:
    if not is_configured():
        return None
    query = urlencode(
        {
            "MerchantLogin": settings.ROBOKASSA_LOGIN,
            "OutSum": out_sum(amount),
            "InvId": str(inv_id),
            "Description": f"Подписка {settings.SUBSCRIPTION_DAYS} дней",
            "SignatureValue": payment_signature(inv_id, amount),
            "Culture": "ru",
        }
    )
    return f"{PAY_URL}?{query}"


def state_signature(inv_id: int) -> str:
    return _md5(f"{settings.ROBOKASSA_LOGIN}:{inv_id}:{settings.ROBOKASSA_PASSWORD2}")


@dataclass
class InvoiceState:
    result_code: int | None
    state_code: int | None

    @property
    def paid(self) -> bool:
        return self.state_code in PAID_STATES

    @property
    def pending(self) -> bool:
        # Счёта ещё нет у Робокассы (код 3) или оплата не завершена.
        return self.result_code == 3 or self.state_code in PENDING_STATES


def parse_state(xml_text: str) -> InvoiceState:
    root = ElementTree.fromstring(xml_text)
    result = _text(root, "Result", "Code")
    state = _text(root, "State", "Code")
    return InvoiceState(
        result_code=int(result) if result and result.isdigit() else None,
        state_code=int(state) if state and state.isdigit() else None,
    )


def _text(root: ElementTree.Element, parent: str, child: str) -> str | None:
    node = root.find(f"m:{parent}/m:{child}", _NS)
    if node is None:
        node = root.find(f"{parent}/{child}")
    if node is None or node.text is None:
        return None
    return node.text.strip()


async def fetch_state(inv_id: int) -> InvoiceState:
    if not is_configured():
        return InvoiceState(result_code=None, state_code=None)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            STATE_URL,
            params={
                "MerchantLogin": settings.ROBOKASSA_LOGIN,
                "InvoiceID": str(inv_id),
                "Signature": state_signature(inv_id),
            },
        )
    response.raise_for_status()
    state = parse_state(response.text)
    log.info("Robokassa счёт %s: result=%s state=%s", inv_id, state.result_code, state.state_code)
    return state


async def charge_recurring(user_id: int, payment_method_id: str, amount: float) -> bool:
    """Автосписания отключены. Метод оставлен, чтобы планировщик ничего не списал."""
    log.info("Автосписание отключено, user_id=%s пропущен", user_id)
    return False
