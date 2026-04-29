"""State persistence with Redis backend and in-memory fallback."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)


class StateStore:
    """Key/value state store. Values are JSON-serializable."""

    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError

    def set(self, key: str, value: Any) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._data.get(key, default)
            return _clone(value)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = _clone(value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisStateStore(StateStore):
    def __init__(self, client: Any, prefix: str = "ts:") -> None:
        self._client = client
        self._prefix = prefix

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._client.get(self._k(key))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            logger.exception("Corrupt JSON in Redis for key %s", key)
            return default

    def set(self, key: str, value: Any) -> None:
        self._client.set(self._k(key), json.dumps(value, default=str))

    def delete(self, key: str) -> None:
        self._client.delete(self._k(key))


def create_state_store(redis_url: Optional[str]) -> StateStore:
    """Build a Redis-backed store, falling back to in-memory if unavailable."""
    if not redis_url:
        logger.info("State store: in-memory (no REDIS_URL configured)")
        return InMemoryStateStore()

    try:
        import redis  # type: ignore

        client = redis.from_url(redis_url, decode_responses=True, socket_timeout=2)
        client.ping()
        logger.info("State store: Redis at %s", redis_url)
        return RedisStateStore(client)
    except Exception as e:
        logger.warning("Redis unavailable (%s) — falling back to in-memory", e)
        return InMemoryStateStore()


def _clone(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.loads(json.dumps(value, default=str))
