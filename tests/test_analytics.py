import analytics


def closed(pnl, close_time="2024-01-01T00:00:00", **extra):
    return {"status": "CLOSED", "pnl": pnl, "close_time": close_time, **extra}


def test_summary_handles_empty_trades():
    s = analytics.summary([], 10_000)
    assert s["total_trades"] == 0
    assert s["win_rate"] == 0
    assert s["profit_factor"] == 0
    assert s["equity_curve"] == [{"timestamp": None, "balance": 10000.0}]


def test_summary_computes_win_rate_and_profit_factor():
    trades = [
        closed(100, "2024-01-01T00:00:00"),
        closed(200, "2024-01-02T00:00:00"),
        closed(-50, "2024-01-03T00:00:00"),
        closed(-50, "2024-01-04T00:00:00"),
    ]
    s = analytics.summary(trades, 10_000)
    assert s["total_trades"] == 4
    assert s["winning_trades"] == 2
    assert s["losing_trades"] == 2
    assert s["win_rate"] == 50.0
    assert s["total_pnl"] == 200.0
    assert s["profit_factor"] == 3.0  # 300 gross profit / 100 gross loss
    assert s["avg_win"] == 150.0
    assert s["avg_loss"] == -50.0


def test_summary_max_drawdown():
    # Equity: 10000 -> 10500 -> 10500 - 800 = 9700  => 7.62% drawdown from peak
    trades = [
        closed(500, "2024-01-01T00:00:00"),
        closed(-800, "2024-01-02T00:00:00"),
    ]
    s = analytics.summary(trades, 10_000)
    assert s["max_drawdown_pct"] == round(800 / 10500 * 100, 2)


def test_summary_ignores_open_trades():
    trades = [
        closed(100, "2024-01-01T00:00:00"),
        {"status": "OPEN", "pnl": 999},
    ]
    s = analytics.summary(trades, 10_000)
    assert s["total_trades"] == 1
    assert s["total_pnl"] == 100.0
