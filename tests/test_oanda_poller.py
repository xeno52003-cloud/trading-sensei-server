from unittest.mock import MagicMock, patch

import pytest
import requests

import oanda_poller
from app_state import AppState
from oanda_client import OandaClient
from state_store import InMemoryStateStore


@pytest.fixture(autouse=True)
def fast_loop(monkeypatch):
    """Trip the loop to exit after one iteration so tests don't hang."""
    calls = {"sleeps": 0}

    def fake_sleep(_seconds):
        calls["sleeps"] += 1
        if calls["sleeps"] >= 1:
            raise SystemExit
    monkeypatch.setattr(oanda_poller.time, "sleep", fake_sleep)
    return calls


@pytest.fixture
def configured_client():
    return OandaClient("https://api-fxpractice.oanda.com", "001-001", "tok")


def _ok(body):
    class R:
        def raise_for_status(self): pass
        def json(self): return body
    return R()


def test_poller_skips_when_unconfigured():
    state = AppState(InMemoryStateStore())
    on_account = MagicMock()
    thread = oanda_poller.start(OandaClient("", "", ""), state, on_account)
    assert thread is None
    on_account.assert_not_called()


def test_poller_invokes_callback_with_synced_account(configured_client):
    state = AppState(InMemoryStateStore())
    on_account = MagicMock()
    response = {
        "account": {"balance": "11000", "unrealizedPL": "50",
                    "marginUsed": "100", "marginAvailable": "10800",
                    "pl": "1000", "openTradeCount": 1, "currency": "USD",
                    "id": "001-001"}
    }
    with patch("oanda_client.requests.get", return_value=_ok(response)):
        thread = oanda_poller.start(configured_client, state, on_account, interval=1)
        thread.join(timeout=2)

    account = state.get_account()
    assert account["balance"] == 11000.0
    assert account["equity"] == 11050.0
    on_account.assert_called_with(account)


def test_poller_defers_to_recent_ea_heartbeat(configured_client):
    from datetime import datetime
    state = AppState(InMemoryStateStore())
    state.update_ea_status(last_heartbeat=datetime.utcnow().isoformat())
    on_account = MagicMock()

    with patch("oanda_client.requests.get") as request_mock:
        thread = oanda_poller.start(configured_client, state, on_account, interval=1)
        thread.join(timeout=2)
        request_mock.assert_not_called()

    on_account.assert_not_called()


def test_poller_swallows_request_errors(configured_client):
    state = AppState(InMemoryStateStore())
    on_account = MagicMock()
    with patch("oanda_client.requests.get",
               side_effect=requests.ConnectionError("boom")):
        thread = oanda_poller.start(configured_client, state, on_account, interval=1)
        thread.join(timeout=2)
    on_account.assert_not_called()


def test_oanda_account_update_evaluates_breaker(server):
    """An account update routed through the poller's callback must let
    the breaker see the fresh equity."""
    server.app.config["TESTING"] = True
    server.breaker.config = server.BreakerConfig(
        daily_loss_pct=0, max_drawdown_pct=10.0,
        max_consecutive_losses=0, starting_balance=10_000,
    )

    # Establish the peak first
    server._account_synced({"balance": 11_000, "equity": 11_000, "open_trades": 0,
                            "unrealized_pnl": 0, "margin_used": 0,
                            "margin_available": 0, "total_pnl": 0})
    assert server.breaker.is_tripped() is False

    # Now equity drops 13.6% from peak — should trip
    server._account_synced({"balance": 9_500, "equity": 9_500, "open_trades": 0,
                            "unrealized_pnl": 0, "margin_used": 0,
                            "margin_available": 0, "total_pnl": 0})
    assert server.breaker.is_tripped() is True
    queue = server.state.drain_ea_commands()
    assert any(c["action"] == "stop" and c["source"] == "breaker" for c in queue)
