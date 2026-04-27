from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import ea_watchdog
from app_state import AppState
from state_store import InMemoryStateStore


def _stale_heartbeat(seconds_ago: int) -> str:
    return (datetime.utcnow() - timedelta(seconds=seconds_ago)).isoformat()


def _fresh_heartbeat() -> str:
    return datetime.utcnow().isoformat()


def _run_n_iterations(monkeypatch, state, on_alert, n, threshold=60):
    """Run the watchdog loop exactly `n` times by raising SystemExit on
    the n-th sleep call."""
    counter = {"i": 0}

    def fake_sleep(_):
        counter["i"] += 1
        if counter["i"] >= n:
            raise SystemExit
    monkeypatch.setattr(ea_watchdog.time, "sleep", fake_sleep)

    thread = ea_watchdog.start(state, on_alert,
                               disconnect_after_sec=threshold,
                               check_interval=1)
    thread.join(timeout=2)


@pytest.fixture
def state():
    return AppState(InMemoryStateStore())


def test_first_iteration_never_alerts(monkeypatch, state):
    state.update_ea_status(last_heartbeat=_stale_heartbeat(120))
    on_alert = MagicMock()
    _run_n_iterations(monkeypatch, state, on_alert, n=1)
    on_alert.assert_not_called()


def test_alive_to_silent_fires_disconnect_alert(monkeypatch, state):
    state.update_ea_status(last_heartbeat=_fresh_heartbeat())
    on_alert = MagicMock()

    counter = {"i": 0}

    def fake_sleep(_):
        counter["i"] += 1
        if counter["i"] == 1:
            # Simulate EA going silent between iterations
            state.update_ea_status(last_heartbeat=_stale_heartbeat(120))
        if counter["i"] >= 2:
            raise SystemExit
    monkeypatch.setattr(ea_watchdog.time, "sleep", fake_sleep)

    thread = ea_watchdog.start(state, on_alert, disconnect_after_sec=60, check_interval=1)
    thread.join(timeout=2)

    assert on_alert.call_count == 1
    title, _msg, kind = on_alert.call_args.args
    assert "disconnected" in title.lower()
    assert kind == "emergency"


def test_silent_to_alive_fires_recovery_alert(monkeypatch, state):
    state.update_ea_status(last_heartbeat=_stale_heartbeat(120))
    on_alert = MagicMock()

    counter = {"i": 0}

    def fake_sleep(_):
        counter["i"] += 1
        if counter["i"] == 1:
            state.update_ea_status(last_heartbeat=_fresh_heartbeat())
        if counter["i"] >= 2:
            raise SystemExit
    monkeypatch.setattr(ea_watchdog.time, "sleep", fake_sleep)

    thread = ea_watchdog.start(state, on_alert, disconnect_after_sec=60, check_interval=1)
    thread.join(timeout=2)

    assert on_alert.call_count == 1
    title, _msg, kind = on_alert.call_args.args
    assert "online" in title.lower()
    assert kind == "info"


def test_steady_state_emits_no_alerts(monkeypatch, state):
    state.update_ea_status(last_heartbeat=_fresh_heartbeat())
    on_alert = MagicMock()

    counter = {"i": 0}

    def fake_sleep(_):
        # keep refreshing the heartbeat — stays alive every iteration
        state.update_ea_status(last_heartbeat=_fresh_heartbeat())
        counter["i"] += 1
        if counter["i"] >= 3:
            raise SystemExit
    monkeypatch.setattr(ea_watchdog.time, "sleep", fake_sleep)

    thread = ea_watchdog.start(state, on_alert, disconnect_after_sec=60, check_interval=1)
    thread.join(timeout=2)
    on_alert.assert_not_called()


def test_threshold_boundary(state):
    state.update_ea_status(last_heartbeat=_stale_heartbeat(45))
    assert ea_watchdog._is_alive(state, threshold_sec=60) is True

    state.update_ea_status(last_heartbeat=_stale_heartbeat(75))
    assert ea_watchdog._is_alive(state, threshold_sec=60) is False


def test_no_heartbeat_recorded_is_silent(state):
    assert ea_watchdog._is_alive(state, threshold_sec=60) is False


def test_corrupt_heartbeat_treated_as_silent(state):
    state.update_ea_status(last_heartbeat="not-a-date")
    assert ea_watchdog._is_alive(state, threshold_sec=60) is False
