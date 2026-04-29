"""Thin OANDA REST v20 client.

Used as a fallback so the dashboard shows account state and open positions
even when the EA isn't connected to push them.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class OandaClient:
    """Read-only client over the OANDA v20 REST API."""

    def __init__(self, base_url: str, account_id: str, token: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.token = token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.account_id and self.token)

    def _get(self, path: str) -> dict[str, Any]:
        res = requests.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept-Datetime-Format": "RFC3339"},
            timeout=self.timeout,
        )
        res.raise_for_status()
        return res.json()

    def account_summary(self) -> dict[str, Any]:
        """Normalised account snapshot matching our internal account schema."""
        body = self._get(f"/v3/accounts/{self.account_id}/summary")
        a = body.get("account", {})
        balance = _f(a.get("balance"))
        unrealized = _f(a.get("unrealizedPL"))
        margin_used = _f(a.get("marginUsed"))
        margin_avail = _f(a.get("marginAvailable"))
        return {
            "balance": round(balance, 2),
            "equity": round(balance + unrealized, 2),
            "margin_used": round(margin_used, 2),
            "margin_available": round(margin_avail, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(_f(a.get("pl")), 2),
            "open_trades": int(a.get("openTradeCount", 0)),
            "currency": a.get("currency"),
            "account_id": a.get("id"),
        }

    def open_trades(self) -> list[dict[str, Any]]:
        body = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        return [_normalise_trade(t) for t in body.get("trades", [])]

    def closed_trades(self, count: int = 500) -> list[dict[str, Any]]:
        """Most-recent closed trades, normalised to our internal schema."""
        body = self._get(f"/v3/accounts/{self.account_id}/trades?state=CLOSED&count={count}")
        return [_normalise_closed_trade(t) for t in body.get("trades", [])]

    def pricing(self, instruments: list[str]) -> list[dict[str, Any]]:
        if not instruments:
            return []
        body = self._get(
            f"/v3/accounts/{self.account_id}/pricing?instruments={','.join(instruments)}"
        )
        out = []
        for p in body.get("prices", []):
            bids = p.get("bids", [{}])
            asks = p.get("asks", [{}])
            bid = _f(bids[0].get("price"))
            ask = _f(asks[0].get("price"))
            out.append({
                "instrument": p.get("instrument"),
                "bid": bid,
                "ask": ask,
                "mid": round((bid + ask) / 2, 5) if bid and ask else None,
                "spread": round(ask - bid, 5) if bid and ask else None,
                "time": p.get("time"),
                "tradeable": p.get("tradeable", False),
            })
        return out


def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _normalise_trade(t: dict[str, Any]) -> dict[str, Any]:
    """Map an OANDA trade record to our internal trade schema."""
    units = _f(t.get("currentUnits"))
    side = "BUY" if units > 0 else "SELL"
    entry = _f(t.get("price"))
    current = _f(t.get("price"))
    sl = _f((t.get("stopLossOrder") or {}).get("price"))
    tp = _f((t.get("takeProfitOrder") or {}).get("price"))
    return {
        "id": str(t.get("id", "")),
        "symbol": t.get("instrument", ""),
        "type": side,
        "lots": round(abs(units) / 100_000, 2),
        "entry_price": entry,
        "current_price": current,
        "stop_loss": sl,
        "take_profit": tp,
        "pnl": round(_f(t.get("unrealizedPL")), 2),
        "pips": 0.0,
        "open_time": t.get("openTime"),
        "status": "OPEN",
        "signal_strength": 0,
        "source": "oanda",
    }


def _normalise_closed_trade(t: dict[str, Any]) -> dict[str, Any]:
    initial_units = _f(t.get("initialUnits"))
    side = "BUY" if initial_units > 0 else "SELL"
    entry = _f(t.get("price"))
    close_price = _f(t.get("averageClosePrice")) or entry
    return {
        "id": str(t.get("id", "")),
        "symbol": t.get("instrument", ""),
        "type": side,
        "lots": round(abs(initial_units) / 100_000, 2),
        "entry_price": entry,
        "current_price": close_price,
        "close_price": close_price,
        "stop_loss": _f((t.get("stopLossOrder") or {}).get("price")),
        "take_profit": _f((t.get("takeProfitOrder") or {}).get("price")),
        "pnl": round(_f(t.get("realizedPL")), 2),
        "pips": 0.0,
        "open_time": t.get("openTime"),
        "close_time": t.get("closeTime"),
        "status": "CLOSED",
        "signal_strength": 0,
    }
