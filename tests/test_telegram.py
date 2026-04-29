from unittest.mock import patch

import pytest

from app_state import AppState
from oanda_client import OandaClient
from state_store import InMemoryStateStore
from telegram_bot import TelegramBot, build_handlers, handle_update
from trade_history import TradeHistory


@pytest.fixture
def deps():
    state = AppState(InMemoryStateStore())
    history = TradeHistory(":memory:")
    oanda = OandaClient("", "", "")
    bot = TelegramBot(token="bot-token", allowed_chat_id="111")
    handlers = build_handlers(state, history, oanda, state.enqueue_ea_command)
    return bot, handlers, state, history


def _update(text, chat_id=111):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_unauthorised_chat_gets_refusal_no_command_runs(deps):
    bot, handlers, state, _ = deps
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/closeall", chat_id=999), handlers)
    send.assert_called_once()
    assert "Unauthorised" in send.call_args.args[1]
    assert state.drain_ea_commands() == []


def test_help_falls_back_for_unknown_command(deps):
    bot, handlers, _, _ = deps
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/whatever"), handlers)
    text = send.call_args.args[1]
    assert "/status" in text and "/balance" in text


def test_closeall_enqueues_command(deps):
    bot, handlers, state, _ = deps
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/closeall"), handlers)
    queue = state.drain_ea_commands()
    assert [c["action"] for c in queue] == ["close_all"]
    assert "queued" in send.call_args.args[1].lower()


def test_close_with_id_enqueues_close_trade(deps):
    bot, handlers, state, _ = deps
    with patch.object(bot, "send"):
        handle_update(bot, _update("/close 42"), handlers)
    queue = state.drain_ea_commands()
    assert queue[0]["action"] == "close_trade"
    assert queue[0]["trade_id"] == "42"


def test_close_without_id_explains_usage(deps):
    bot, handlers, state, _ = deps
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/close"), handlers)
    assert "Usage" in send.call_args.args[1]
    assert state.drain_ea_commands() == []


def test_status_reports_account_and_ea_state(deps):
    bot, handlers, state, _ = deps
    state.update_ea_status(running=True, connected=True, symbol="XAUUSD")
    state.update_account(balance=12345.67, equity=12400.00, open_trades=2)
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/status"), handlers)
    text = send.call_args.args[1]
    assert "12345.67" in text
    assert "running" in text
    assert "XAUUSD" in text


def test_stats_uses_history(deps):
    bot, handlers, _, history = deps
    history.record_close({
        "id": "1", "symbol": "X", "type": "BUY", "lots": 0.1,
        "entry_price": 10, "close_price": 12, "stop_loss": 9, "take_profit": 13,
        "pnl": 100.0, "pips": 0, "open_time": "2024-01-01", "close_time": "2024-01-02",
        "signal_strength": 5,
    })
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/stats"), handlers)
    text = send.call_args.args[1]
    assert "100.00" in text or "100.0" in text
    assert "Trades: 1" in text


def test_handler_exception_replies_with_error(deps):
    bot, handlers, _, _ = deps
    handlers["status"] = lambda _args: (_ for _ in ()).throw(RuntimeError("nope"))
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("/status"), handlers)
    assert "nope" in send.call_args.args[1]


def test_non_command_text_redirects_to_help(deps):
    bot, handlers, _, _ = deps
    with patch.object(bot, "send") as send:
        handle_update(bot, _update("hello"), handlers)
    assert "/help" in send.call_args.args[1]
