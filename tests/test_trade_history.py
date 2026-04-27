import pytest

from trade_history import TradeHistory, open_history


@pytest.fixture
def hist():
    return TradeHistory(":memory:")


def _trade(**overrides):
    base = {
        "id": "t1", "symbol": "XAUUSD", "type": "BUY", "lots": 0.1,
        "entry_price": 2050.0, "close_price": 2060.0, "stop_loss": 2040.0,
        "take_profit": 2080.0, "pnl": 100.0, "pips": 100.0,
        "open_time": "2026-04-27T08:00:00", "close_time": "2026-04-27T09:00:00",
        "signal_strength": 5,
    }
    base.update(overrides)
    return base


def test_record_and_list(hist):
    hist.record_close(_trade(id="a"))
    hist.record_close(_trade(id="b", close_time="2026-04-27T10:00:00"))
    rows = hist.list_closed()
    assert [r["id"] for r in rows] == ["b", "a"]   # newest first
    assert all(r["status"] == "CLOSED" for r in rows)


def test_record_is_idempotent(hist):
    hist.record_close(_trade(id="dup", pnl=10))
    hist.record_close(_trade(id="dup", pnl=20))
    rows = hist.list_closed()
    assert len(rows) == 1
    assert rows[0]["pnl"] == 20.0


def test_limit_caps_results(hist):
    for i in range(5):
        hist.record_close(_trade(id=str(i), close_time=f"2026-04-27T0{i}:00:00"))
    assert len(hist.list_closed(limit=3)) == 3


def test_open_history_url_schemes():
    assert open_history("sqlite://:memory:").count() == 0
    with pytest.raises(ValueError):
        open_history("postgres://x")
