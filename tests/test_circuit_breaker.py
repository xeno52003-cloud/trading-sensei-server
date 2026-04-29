from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app_state import AppState
from circuit_breaker import BreakerConfig, CircuitBreaker
from state_store import InMemoryStateStore
from trade_history import TradeHistory


@pytest.fixture
def fixtures():
    state = AppState(InMemoryStateStore())
    history = TradeHistory(":memory:")
    return state, history


def _config(**overrides):
    base = dict(daily_loss_pct=5.0, max_drawdown_pct=20.0,
                max_consecutive_losses=5, starting_balance=10_000.0)
    base.update(overrides)
    return BreakerConfig(**base)


_counter = {"n": 0}


def _close(history, pnl, when=None):
    _counter["n"] += 1
    history.record_close({
        "id": f"t{_counter['n']}",
        "symbol": "X", "type": "BUY", "lots": 0.1,
        "entry_price": 1, "close_price": 1, "stop_loss": 0, "take_profit": 0,
        "pnl": pnl, "pips": 0,
        "open_time": (when or datetime.utcnow().isoformat()),
        "close_time": (when or datetime.utcnow().isoformat()),
        "signal_strength": 5,
    })


def test_safe_when_no_threshold_breached(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config())
    _close(history, 50)
    assert breaker.evaluate({"equity": 10_050}) is None


def test_trips_on_daily_loss_limit(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(daily_loss_pct=2.0))
    _close(history, -250)  # -250 of 10k = -2.5%, exceeds 2%
    reason = breaker.evaluate({"equity": 9_750})
    assert reason and "Daily loss" in reason


def test_does_not_trip_on_yesterdays_losses(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(daily_loss_pct=2.0))
    _close(history, -500, when="2020-01-01T00:00:00")
    assert breaker.evaluate({"equity": 9_500}) is None


def test_trips_on_consecutive_losses(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(max_consecutive_losses=3))
    for i in range(3):
        _close(history, -10, when=f"2026-04-27T0{i}:00:00")
    reason = breaker.evaluate({"equity": 9_970})
    assert reason and "Consecutive" in reason


def test_streak_resets_on_winning_trade(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(max_consecutive_losses=3))
    _close(history, -10, when="2026-04-27T01:00:00")
    _close(history, -10, when="2026-04-27T02:00:00")
    _close(history, 50,  when="2026-04-27T03:00:00")  # most recent — winner
    assert breaker.evaluate({"equity": 10_030}) is None


def test_trips_on_drawdown(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(max_drawdown_pct=10.0))
    breaker.evaluate({"equity": 11_000})        # peak set
    reason = breaker.evaluate({"equity": 9_500}) # 13.6% drawdown
    assert reason and "Drawdown" in reason


def test_trip_is_idempotent_and_enqueues_stop(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config())
    enqueue, alert = MagicMock(), MagicMock()

    breaker.trip("test reason", enqueue, alert)
    breaker.trip("test reason", enqueue, alert)  # idempotent

    enqueue.assert_called_once()
    assert enqueue.call_args.args[0]["action"] == "stop"
    assert enqueue.call_args.args[0]["source"] == "breaker"
    alert.assert_called_once()
    assert breaker.is_tripped()


def test_reset_clears_state(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config())
    breaker.trip("x", MagicMock(), MagicMock())
    assert breaker.reset() is True
    assert breaker.is_tripped() is False
    # Reset is a no-op when not tripped
    assert breaker.reset() is False


def test_status_includes_metrics(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config())
    _close(history, -50)
    s = breaker.status({"equity": 9_950})
    assert s["tripped"] is False
    assert s["today_pnl"] == -50.0
    assert s["losing_streak"] == 1
    assert s["config"]["daily_loss_pct"] == 5.0


def test_zero_threshold_disables_rule(fixtures):
    state, history = fixtures
    breaker = CircuitBreaker(state, history, _config(
        daily_loss_pct=0, max_drawdown_pct=0, max_consecutive_losses=0,
    ))
    _close(history, -10_000)  # would normally trip every rule
    assert breaker.evaluate({"equity": 0}) is None
