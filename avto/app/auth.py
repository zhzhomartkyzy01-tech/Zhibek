"""Вход по PIN: хэш с солью, сессии на 12 часов, блокировка после 5 ошибок."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

SESSION_HOURS = 12
MAX_FAILS = 5
LOCK_MINUTES = 15
PBKDF2_ROUNDS = 100_000

# Права по ролям (раздел 2 ТЗ). Сервер проверяет их в каждом запросе.
PERMISSIONS = {
    "manager": {"trips_edit", "leads_edit", "reports_own", "export_1c"},
    "head": {"trips_edit", "leads_edit", "reports_own", "reports_all", "reports_edit_all", "export_1c",
             "reassign", "delete", "view_all"},
    "exec": {"reports_all", "export_1c", "view_all"},
    "admin": {"trips_edit", "leads_edit", "reports_own", "reports_all", "reports_edit_all", "export_1c",
              "reassign", "delete", "view_all", "admin"},
}


def valid_pin(pin) -> bool:
    return isinstance(pin, str) and re.fullmatch(r"\d{4,6}", pin) is not None


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    """SHA-256 с солью (PBKDF2-HMAC-SHA256, 100 000 повторов). Возвращает (хэш, соль)."""
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return h, salt


def check_pin(pin: str, pin_hash: str | None, salt: str | None) -> bool:
    if not pin_hash or not salt or not valid_pin(pin):
        return False
    return hmac.compare_digest(hash_pin(pin, salt)[0], pin_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry(now: datetime) -> datetime:
    return now + timedelta(hours=SESSION_HOURS)


def lock_until(now: datetime) -> datetime:
    return now + timedelta(minutes=LOCK_MINUTES)


def can(user: dict, perm: str) -> bool:
    return perm in PERMISSIONS.get(user["role"], set())
