"""Seed AppState + TradeHistory with believable fake data so the dashboard
is interactive out of the box. Activated by DEMO=1.

Idempotent: a flag in the StateStore prevents re-seeding on every boot,
so editing trades from the dashboard isn't undone by a restart.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from app_state import AppState, new_alert
from trade_history import TradeHistory

logger = logging.getLogger(__name__)


def seed(state: AppState, history: TradeHistory, force: bool = False) -> None:
    if not force and state.store.get("demo_seeded"):
        return

    rng = random.Random(42)

    state.update_account(
        balance=10_850.00,
        equity=10_937.50,
        margin_used=205.05,
        margin_available=10_732.45,
        unrealized_pnl=87.50,
        total_pnl=850.00,
        open_trades=1,
    )
    state.update_ea_status(
        running=True,
        connected=True,
        symbol="XAUUSD",
        timeframe="H1",
        last_heartbeat=datetime.utcnow().isoformat(),
        version="2.0.0-demo",
        uptime=12_345,
    )

    state.add_trade({
        "id": "demo-open-1",
        "symbol": "XAUUSD", "type": "BUY", "lots": 0.10,
        "entry_price": 2050.50, "current_price": 2059.25,
        "stop_loss": 2040.25, "take_profit": 2081.26,
        "pnl": 87.50, "pips": 87.5,
        "open_time": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
        "status": "OPEN", "signal_strength": 5,
    })

    base = datetime.utcnow() - timedelta(days=14)
    for i in range(20):
        is_win = rng.random() > 0.4
        pnl = round(rng.uniform(20, 150) if is_win else -rng.uniform(15, 80), 2)
        opened = base + timedelta(hours=i * 12 + rng.randint(0, 6))
        closed = opened + timedelta(hours=rng.uniform(1, 6))
        history.record_close({
            "id": f"demo-{i:02d}",
            "symbol": "XAUUSD",
            "type": rng.choice(["BUY", "SELL"]),
            "lots": round(rng.uniform(0.05, 0.15), 2),
            "entry_price": round(rng.uniform(2030, 2060), 2),
            "close_price": round(rng.uniform(2030, 2070), 2),
            "stop_loss": 0, "take_profit": 0,
            "pnl": pnl, "pips": round(pnl * 0.8, 1),
            "open_time": opened.isoformat(),
            "close_time": closed.isoformat(),
            "signal_strength": rng.randint(3, 7),
        })

    seed_alerts = [
        ("🟢 BUY XAUUSD", "Entry: 2050.50 | Lots: 0.10 | Signal: 5/7", "buy"),
        ("🔒 Breakeven Set", "BUY XAUUSD moved to breakeven at 2050.50", "info"),
        ("✅ Closed +$54.00", "SELL XAUUSD @ 2048.50", "profit"),
        ("❌ Closed -$32.50", "BUY XAUUSD hit SL at 2038.25", "loss"),
        ("🤖 EA Started", "Trading Sensei v2.0.0-demo on XAUUSD H1", "info"),
    ]
    for i, (title, message, kind) in enumerate(seed_alerts):
        alert = new_alert(title, message, kind)
        alert["id"] = f"demo-alert-{i}"
        state.add_alert(alert)

    state.store.set("demo_seeded", True)
    logger.info("Demo data seeded: 20 closed trades, 1 open, 5 alerts")
