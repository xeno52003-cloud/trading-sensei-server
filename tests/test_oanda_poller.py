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
    emit = MagicMock()
    thread = oanda_poller.start(OandaClient("", "", ""), state, emit)
    assert thread is None
    emit.assert_not_called()


def test_poller_updates_account_and_emits(configured_client):
    state = AppState(InMemoryStateStore())
    emit = MagicMock()
    response = {
        "account": {"balance": "11000", "unrealizedPL": "50",
                    "marginUsed": "100", "marginAvailable": "10800",
                    "pl": "1000", "openTradeCount": 1, "currency": "USD",
                    "id": "001-001"}
    }
    with patch("oanda_client.requests.get", return_value=_ok(response)):
        thread = oanda_poller.start(configured_client, state, emit, interval=1)
        thread.join(timeout=2)

    account = state.get_account()
    assert account["balance"] == 11000.0
    assert account["equity"] == 11050.0
    emit.assert_called_with("account_update", account)


def test_poller_defers_to_recent_ea_heartbeat(configured_client):
    from datetime import datetime
    state = AppState(InMemoryStateStore())
    state.update_ea_status(last_heartbeat=datetime.utcnow().isoformat())
    emit = MagicMock()

    with patch("oanda_client.requests.get") as request_mock:
        thread = oanda_poller.start(configured_client, state, emit, interval=1)
        thread.join(timeout=2)
        request_mock.assert_not_called()

    emit.assert_not_called()


def test_poller_swallows_request_errors(configured_client):
    state = AppState(InMemoryStateStore())
    emit = MagicMock()
    with patch("oanda_client.requests.get",
               side_effect=requests.ConnectionError("boom")):
        thread = oanda_poller.start(configured_client, state, emit, interval=1)
        thread.join(timeout=2)
    emit.assert_not_called()
