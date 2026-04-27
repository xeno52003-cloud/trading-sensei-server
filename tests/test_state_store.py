from app_state import AppState
from state_store import InMemoryStateStore, create_state_store


def test_in_memory_store_isolates_writes():
    store = InMemoryStateStore()
    store.set("k", {"a": 1})

    fetched = store.get("k")
    fetched["a"] = 99  # mutating the fetched copy must not change the stored value

    assert store.get("k") == {"a": 1}


def test_create_state_store_falls_back_when_redis_unavailable():
    store = create_state_store("redis://127.0.0.1:1")  # nothing listening here
    assert isinstance(store, InMemoryStateStore)


def test_create_state_store_returns_in_memory_when_no_url():
    store = create_state_store(None)
    assert isinstance(store, InMemoryStateStore)


def test_app_state_trade_lifecycle():
    state = AppState(InMemoryStateStore())
    state.add_trade({"id": "t1", "status": "OPEN", "symbol": "XAUUSD", "type": "BUY",
                     "lots": 0.1, "entry_price": 2000, "current_price": 2000,
                     "stop_loss": 1990, "take_profit": 2030, "pnl": 0, "pips": 0})

    assert len(state.get_open_trades()) == 1
    assert state.find_trade("t1")["symbol"] == "XAUUSD"

    state.update_trade("t1", status="CLOSED", pnl=42)
    assert state.get_open_trades() == []
    assert state.get_closed_trades()[0]["pnl"] == 42


def test_app_state_alerts_capped_at_100():
    state = AppState(InMemoryStateStore())
    for i in range(120):
        state.add_alert({"id": str(i), "title": "x", "message": "y", "type": "info",
                         "timestamp": "2024-01-01", "read": False})
    alerts = state.get_alerts()
    assert len(alerts) == 100
    # Newest first
    assert alerts[0]["id"] == "119"


def test_mark_alerts_read():
    state = AppState(InMemoryStateStore())
    state.add_alert({"id": "a", "title": "x", "message": "y", "type": "info",
                     "timestamp": "2024-01-01", "read": False})
    state.add_alert({"id": "b", "title": "x", "message": "y", "type": "info",
                     "timestamp": "2024-01-01", "read": False})

    marked = state.mark_alerts_read(["a"])
    assert marked == 1
    by_id = {a["id"]: a for a in state.get_alerts()}
    assert by_id["a"]["read"] is True
    assert by_id["b"]["read"] is False


def test_pop_ea_command_clears_after_read():
    state = AppState(InMemoryStateStore())
    state.set_ea_command({"action": "start"})
    assert state.pop_ea_command() == {"action": "start"}
    assert state.pop_ea_command() is None
