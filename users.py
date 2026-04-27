"""User auth: bcrypt-hashed PINs with simple lockout."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import bcrypt

from state_store import StateStore

logger = logging.getLogger(__name__)

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_SEC = 300


class UserStore:
    """Single-user-friendly store keyed by user_id. Supports multi-user too."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def _users(self) -> dict[str, Any]:
        return self.store.get("users", {})

    def _save(self, users: dict[str, Any]) -> None:
        self.store.set("users", users)

    def exists(self, user_id: str) -> bool:
        return user_id in self._users()

    def create(self, user_id: str, pin: str) -> None:
        if not _is_valid_pin(pin):
            raise ValueError("PIN must be 6 digits")
        users = self._users()
        users[user_id] = {
            "pin_hash": _hash_pin(pin),
            "failed_attempts": [],
            "created_at": time.time(),
        }
        self._save(users)

    def verify(self, user_id: str, pin: str) -> tuple[bool, Optional[str]]:
        """Returns (ok, error). Tracks failed attempts within LOCKOUT_WINDOW_SEC."""
        users = self._users()
        user = users.get(user_id)
        if not user:
            return False, "Unknown user"

        now = time.time()
        recent = [t for t in user.get("failed_attempts", []) if now - t < LOCKOUT_WINDOW_SEC]
        if len(recent) >= LOCKOUT_THRESHOLD:
            return False, "Too many failed attempts — try again in a few minutes"

        if not _check_pin(pin, user["pin_hash"]):
            recent.append(now)
            user["failed_attempts"] = recent
            self._save(users)
            return False, "Invalid PIN"

        user["failed_attempts"] = []
        user["last_login"] = now
        self._save(users)
        return True, None

    def change_pin(self, user_id: str, current_pin: str, new_pin: str) -> tuple[bool, Optional[str]]:
        ok, err = self.verify(user_id, current_pin)
        if not ok:
            return False, err
        if not _is_valid_pin(new_pin):
            return False, "PIN must be 6 digits"
        users = self._users()
        users[user_id]["pin_hash"] = _hash_pin(new_pin)
        self._save(users)
        return True, None


def bootstrap_admin(users: UserStore, admin_pin: str, user_id: str = "admin") -> bool:
    """Create the admin user from ADMIN_PIN if not yet provisioned."""
    if not admin_pin:
        return False
    if users.exists(user_id):
        return False
    if not _is_valid_pin(admin_pin):
        logger.error("ADMIN_PIN must be 6 digits — refusing to bootstrap")
        return False
    users.create(user_id, admin_pin)
    logger.info("Bootstrapped %s user from ADMIN_PIN", user_id)
    return True


def _is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 6 and pin.isdigit()


def _hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(rounds=10)).decode()


def _check_pin(pin: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode(), stored_hash.encode())
    except (ValueError, TypeError):
        return False
