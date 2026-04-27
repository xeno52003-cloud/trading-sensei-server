"""Domain-specific accessors over the StateStore."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from state_store import StateStore

_DEFAULT_EA_STATUS = {
    "running": False,
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "last_signal": None,
    "last_trade_time": None,
    "version": "2.0.0",
    "uptime": 0,
    "connected": False,
    "last_heartbeat": None,
}

_DEFAULT_ACCOUNT = {
    "balance": 10000.00,
    "equity": 10000.00,
    "margin_used": 0,
    "margin_available": 10000.00,
    "unrealized_pnl": 0,
    "total_pnl": 0,
    "open_trades": 0,
}

ALERT_CAP = 100


class AppState:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.connected_devices: set[str] = set()

    # --- ea status ---

    def get_ea_status(self) -> dict[str, Any]:
        return self.store.get("ea_status", dict(_DEFAULT_EA_STATUS))

    def update_ea_status(self, **fields: Any) -> dict[str, Any]:
        current = self.get_ea_status()
        current.update(fields)
        self.store.set("ea_status", current)
        return current

    # --- account ---

    def get_account(self) -> dict[str, Any]:
        return self.store.get("account", dict(_DEFAULT_ACCOUNT))

    def update_account(self, **fields: Any) -> dict[str, Any]:
        current = self.get_account()
        current.update(fields)
        self.store.set("account", current)
        return current

    # --- trades ---

    def get_trades(self) -> list[dict[str, Any]]:
        return self.store.get("trades", [])

    def get_open_trades(self) -> list[dict[str, Any]]:
        return [t for t in self.get_trades() if t.get("status") == "OPEN"]

    def get_closed_trades(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        closed = [t for t in self.get_trades() if t.get("status") == "CLOSED"]
        return closed[-limit:] if limit else closed

    def find_trade(self, trade_id: str) -> Optional[dict[str, Any]]:
        for t in self.get_trades():
            if t.get("id") == trade_id:
                return t
        return None

    def add_trade(self, trade: dict[str, Any]) -> None:
        trades = self.get_trades()
        trades.append(trade)
        self.store.set("trades", trades)

    def update_trade(self, trade_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        trades = self.get_trades()
        for t in trades:
            if t.get("id") == trade_id:
                t.update(fields)
                self.store.set("trades", trades)
                return t
        return None

    # --- alerts ---

    def get_alerts(self, limit: Optional[int] = None) -> list[dict[str, Any]]:
        alerts = self.store.get("alerts", [])
        return alerts[:limit] if limit else alerts

    def add_alert(self, alert: dict[str, Any]) -> None:
        alerts = self.store.get("alerts", [])
        alerts.insert(0, alert)
        self.store.set("alerts", alerts[:ALERT_CAP])

    def mark_alerts_read(self, alert_ids: list[str]) -> int:
        alerts = self.store.get("alerts", [])
        marked = 0
        for a in alerts:
            if a.get("id") in alert_ids and not a.get("read"):
                a["read"] = True
                marked += 1
        if marked:
            self.store.set("alerts", alerts)
        return marked

    def clear_alerts(self) -> None:
        self.store.set("alerts", [])

    # --- device tokens ---

    def register_device(self, device_id: str, token: str, platform: str) -> None:
        tokens = self.store.get("device_tokens", {})
        tokens[device_id] = {
            "token": token,
            "platform": platform,
            "registered_at": datetime.utcnow().isoformat(),
        }
        self.store.set("device_tokens", tokens)

    def get_device_tokens(self) -> dict[str, Any]:
        return self.store.get("device_tokens", {})

    # --- ea command (transient) ---

    def set_ea_command(self, command: dict[str, Any]) -> None:
        self.store.set("ea_command", command)

    def pop_ea_command(self) -> Optional[dict[str, Any]]:
        command = self.store.get("ea_command")
        if command is not None:
            self.store.delete("ea_command")
        return command


def new_alert(title: str, message: str, alert_type: str) -> dict[str, Any]:
    return {
        "id": str(int(time.time() * 1000)),
        "title": title,
        "message": message,
        "type": alert_type,
        "timestamp": datetime.utcnow().isoformat(),
        "read": False,
    }
