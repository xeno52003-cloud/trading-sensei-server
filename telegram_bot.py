"""Inbound Telegram bot — handle commands the user sends from Telegram.

Telegram POSTs every chat update to /webhook/telegram/<secret>. We respond
with sendMessage. Only the configured TELEGRAM_CHAT_ID is allowed; others
get a polite refusal.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, token: str, allowed_chat_id: str) -> None:
        self.token = token
        self.allowed_chat_id = str(allowed_chat_id) if allowed_chat_id else ""

    @property
    def configured(self) -> bool:
        return bool(self.token and self.allowed_chat_id)

    def send(self, chat_id: str, text: str) -> None:
        if not self.token:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=5,
            )
        except requests.RequestException as e:
            logger.warning("Telegram send error: %s", e)

    def is_authorised(self, chat_id: Any) -> bool:
        return str(chat_id) == self.allowed_chat_id


def handle_update(bot: TelegramBot, update: dict[str, Any], handlers: dict[str, Callable[[list[str]], str]]) -> None:
    """Dispatch a Telegram Update to the matching command handler."""
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return

    if not bot.is_authorised(chat_id):
        bot.send(chat_id, "🚫 Unauthorised — this bot is private.")
        logger.warning("Telegram unauthorised user %s sent: %s", chat_id, text)
        return

    if not text.startswith("/"):
        bot.send(chat_id, "Use a command — try /help")
        return

    parts = text.split()
    command = parts[0].lstrip("/").split("@", 1)[0].lower()
    args = parts[1:]

    handler = handlers.get(command) or handlers.get("help")
    try:
        reply = handler(args) if handler else "Unknown command. Try /help"
    except Exception as e:
        logger.exception("Telegram handler %s failed", command)
        reply = f"⚠️ {e}"

    bot.send(chat_id, reply)


def build_handlers(state, history, oanda, enqueue_command: Callable[[dict[str, Any]], None]) -> dict[str, Callable[[list[str]], str]]:
    """Build the command map. Pure functions of the injected services."""

    def cmd_help(_args):
        return (
            "🥷 *Trading Sensei*\n"
            "/status — EA + account snapshot\n"
            "/balance — account balance\n"
            "/trades — open trades\n"
            "/stats — analytics summary\n"
            "/start_ea, /stop_ea — toggle the EA\n"
            "/closeall — emergency close every position\n"
            "/close <id> — close one trade\n"
        )

    def cmd_status(_args):
        ea = state.get_ea_status()
        acc = state.get_account()
        return (
            f"🤖 EA: *{'running' if ea.get('running') else 'idle'}* "
            f"({'connected' if ea.get('connected') else 'offline'})\n"
            f"📊 Symbol: `{ea.get('symbol')}` / `{ea.get('timeframe')}`\n"
            f"💵 Balance: `${acc.get('balance', 0):.2f}`\n"
            f"📈 Equity:  `${acc.get('equity', 0):.2f}`\n"
            f"🟢 Open trades: {acc.get('open_trades', 0)}"
        )

    def cmd_balance(_args):
        try:
            acc = oanda.account_summary() if oanda.configured else state.get_account()
        except Exception:
            acc = state.get_account()
        return (
            f"💰 *Account*\n"
            f"Balance: `${acc.get('balance', 0):.2f}`\n"
            f"Equity:  `${acc.get('equity', 0):.2f}`\n"
            f"Margin used: `${acc.get('margin_used', 0):.2f}`\n"
            f"Available:   `${acc.get('margin_available', 0):.2f}`"
        )

    def cmd_trades(_args):
        trades = state.get_open_trades()
        if not trades:
            return "No open trades."
        lines = ["📊 *Open trades*"]
        for t in trades:
            sign = "+" if (t.get("pnl", 0) or 0) >= 0 else ""
            lines.append(
                f"• `{t['id']}` {t['type']} {t['symbol']} {t['lots']} lots "
                f"@ {t['entry_price']} ({sign}${(t.get('pnl') or 0):.2f})"
            )
        return "\n".join(lines)

    def cmd_stats(_args):
        import analytics
        all_trades = history.all_closed() + state.get_open_trades()
        s = analytics.summary(all_trades, 10_000)
        return (
            f"📈 *Stats*\n"
            f"Trades: {s['total_trades']} | Win rate: {s['win_rate']}%\n"
            f"P&L: `${s['total_pnl']:.2f}` | PF: {s['profit_factor']}\n"
            f"Max DD: {s['max_drawdown_pct']}%"
        )

    def cmd_close_all(_args):
        enqueue_command({"action": "close_all", "source": "telegram"})
        return "🚨 Close-all queued — EA will execute on its next heartbeat."

    def cmd_close(args):
        if not args:
            return "Usage: /close <trade_id>"
        enqueue_command({"action": "close_trade", "trade_id": args[0], "source": "telegram"})
        return f"✅ Close queued for `{args[0]}`."

    def cmd_start_ea(_args):
        enqueue_command({"action": "start", "source": "telegram"})
        return "▶️ EA start queued."

    def cmd_stop_ea(_args):
        enqueue_command({"action": "stop", "source": "telegram"})
        return "⏸️ EA stop queued."

    return {
        "help": cmd_help,
        "start": cmd_help,
        "status": cmd_status,
        "balance": cmd_balance,
        "trades": cmd_trades,
        "stats": cmd_stats,
        "closeall": cmd_close_all,
        "close": cmd_close,
        "start_ea": cmd_start_ea,
        "stop_ea": cmd_stop_ea,
    }
