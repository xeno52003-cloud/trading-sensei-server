"""Server-side risk circuit breaker.

Independent of the EA's own daily-loss check — this is the second line of
defence. Trips on any of:

  • daily realised loss exceeds RISK_DAILY_LOSS_PCT of starting balance
  • account drawdown from peak exceeds RISK_MAX_DRAWDOWN_PCT
  • RISK_MAX_CONSECUTIVE_LOSSES losing trades in a row

When tripped, the breaker enqueues an EA `stop` command and stays tripped
until the operator hits POST /api/risk/reset. The EA keeps polling, sees
`stop` on its next heartbeat, and stops opening new trades.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BreakerConfig:
    daily_loss_pct: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    starting_balance: float


class CircuitBreaker:
    def __init__(self, state, history, config: BreakerConfig) -> None:
        self.state = state
        self.history = history
        self.config = config

    # --- evaluation --------------------------------------------------------

    def evaluate(self, account: dict[str, Any]) -> Optional[str]:
        """Return a reason string if the breaker should trip, else None."""
        cfg = self.config

        if cfg.daily_loss_pct > 0:
            today_pnl = self._today_pnl()
            limit = -cfg.starting_balance * cfg.daily_loss_pct / 100
            if today_pnl <= limit:
                return f"Daily loss {today_pnl:.2f} ≤ {limit:.2f}"

        if cfg.max_drawdown_pct > 0:
            peak = self._update_peak(account)
            equity = float(account.get("equity") or 0)
            if peak > 0:
                drawdown = (peak - equity) / peak * 100
                if drawdown >= cfg.max_drawdown_pct:
                    return f"Drawdown {drawdown:.2f}% ≥ {cfg.max_drawdown_pct}%"

        if cfg.max_consecutive_losses > 0:
            streak = self._losing_streak()
            if streak >= cfg.max_consecutive_losses:
                return f"Consecutive losses: {streak}"

        return None

    # --- state -------------------------------------------------------------

    def is_tripped(self) -> bool:
        return self.state.store.get("breaker_tripped") is not None

    def status(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "tripped": self.is_tripped(),
            "tripped_at": self.state.store.get("breaker_tripped"),
            "today_pnl": round(self._today_pnl(), 2),
            "peak_balance": round(self._peak_balance(), 2),
            "current_equity": round(float(account.get("equity") or 0), 2),
            "losing_streak": self._losing_streak(),
            "config": {
                "daily_loss_pct": self.config.daily_loss_pct,
                "max_drawdown_pct": self.config.max_drawdown_pct,
                "max_consecutive_losses": self.config.max_consecutive_losses,
                "starting_balance": self.config.starting_balance,
            },
        }

    def trip(
        self,
        reason: str,
        enqueue: Callable[[dict[str, Any]], None],
        alert: Callable[[str, str], None],
    ) -> None:
        if self.is_tripped():
            return
        record = {"reason": reason, "time": datetime.utcnow().isoformat()}
        self.state.store.set("breaker_tripped", record)
        enqueue({"action": "stop", "source": "breaker", "reason": reason})
        alert("🚨 Circuit breaker tripped", reason)
        logger.warning("Circuit breaker tripped: %s", reason)

    def reset(self) -> bool:
        if not self.is_tripped():
            return False
        self.state.store.delete("breaker_tripped")
        logger.info("Circuit breaker reset")
        return True

    # --- helpers -----------------------------------------------------------

    def _today_pnl(self) -> float:
        today = datetime.utcnow().date().isoformat()
        return sum(
            float(t.get("pnl") or 0)
            for t in self.history.all_closed()
            if (t.get("close_time") or "")[:10] == today
        )

    def _losing_streak(self) -> int:
        streak = 0
        for trade in self.history.list_closed(limit=100):
            if float(trade.get("pnl") or 0) < 0:
                streak += 1
            else:
                break
        return streak

    def _peak_balance(self) -> float:
        return float(self.state.store.get("peak_balance") or self.config.starting_balance)

    def _update_peak(self, account: dict[str, Any]) -> float:
        peak = self._peak_balance()
        equity = float(account.get("equity") or 0)
        if equity > peak:
            self.state.store.set("peak_balance", equity)
            return equity
        return peak
