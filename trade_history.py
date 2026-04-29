"""Durable closed-trade history backed by SQLite.

Open trades stay in the StateStore (cheap to lose, change every tick).
Closed trades are append-only ledger data — they belong on disk so analytics
keeps working across restarts.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS closed_trades (
    id              TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    type            TEXT NOT NULL,
    lots            REAL,
    entry_price     REAL,
    close_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    pnl             REAL,
    pips            REAL,
    open_time       TEXT,
    close_time      TEXT,
    signal_strength INTEGER
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_close_time ON closed_trades(close_time);
"""

_COLUMNS = (
    "id", "symbol", "type", "lots", "entry_price", "close_price",
    "stop_loss", "take_profit", "pnl", "pips", "open_time", "close_time",
    "signal_strength",
)


class TradeHistory:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def record_close(self, trade: dict[str, Any]) -> None:
        row = tuple(trade.get(col) for col in _COLUMNS)
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO closed_trades ({','.join(_COLUMNS)}) "
                f"VALUES ({','.join(['?'] * len(_COLUMNS))})",
                row,
            )

    def list_closed(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM closed_trades "
                "ORDER BY COALESCE(close_time, open_time) DESC LIMIT ?",
                (limit,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            r["status"] = "CLOSED"
        return rows

    def all_closed(self) -> list[dict[str, Any]]:
        return self.list_closed(limit=10_000)

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM closed_trades").fetchone()[0]


def open_history(database_url: Optional[str]) -> TradeHistory:
    """`sqlite:///path/to.db`, `sqlite://:memory:`, or None → ./data/trades.db."""
    if not database_url:
        return TradeHistory("data/trades.db")
    if database_url.startswith("sqlite:///"):
        return TradeHistory(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return TradeHistory(database_url.removeprefix("sqlite://"))
    raise ValueError(f"Unsupported DATABASE_URL scheme: {database_url}")
