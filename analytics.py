"""Analytics computed from the trade history."""

from __future__ import annotations

from typing import Any


def summary(trades: list[dict[str, Any]], starting_balance: float) -> dict[str, Any]:
    """Compute aggregate stats and an equity curve from closed trades."""
    closed = [t for t in trades if t.get("status") == "CLOSED"]

    pnls = [float(t.get("pnl", 0) or 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_pnl = sum(pnls)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    total = len(closed)
    win_count = len(wins)
    loss_count = len(losses)

    win_rate = (win_count / total * 100) if total else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss else (gross_profit if gross_profit else 0.0)
    avg_win = (gross_profit / win_count) if win_count else 0.0
    avg_loss = (-gross_loss / loss_count) if loss_count else 0.0
    avg_trade = (total_pnl / total) if total else 0.0

    equity_curve = _equity_curve(closed, starting_balance)
    max_drawdown_pct = _max_drawdown(equity_curve)

    return {
        "total_trades": total,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_trade": round(avg_trade, 2),
        "largest_win": round(max(wins), 2) if wins else 0.0,
        "largest_loss": round(min(losses), 2) if losses else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "equity_curve": equity_curve,
    }


def _equity_curve(closed: list[dict[str, Any]], starting_balance: float) -> list[dict[str, Any]]:
    ordered = sorted(closed, key=lambda t: t.get("close_time") or t.get("open_time") or "")
    balance = float(starting_balance)
    curve = [{"timestamp": None, "balance": round(balance, 2)}]
    for t in ordered:
        balance += float(t.get("pnl", 0) or 0)
        curve.append({
            "timestamp": t.get("close_time") or t.get("open_time"),
            "balance": round(balance, 2),
        })
    return curve


def _max_drawdown(equity_curve: list[dict[str, Any]]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for point in equity_curve:
        balance = point["balance"]
        if balance > peak:
            peak = balance
        if peak > 0:
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd
