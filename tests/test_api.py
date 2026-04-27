def test_health_check_is_public(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "healthy"


def test_dashboard_is_served_at_root(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Trading Sensei" in res.data


def test_login_rejects_short_pin(client):
    res = client.post("/api/auth/login", json={"pin": "123"})
    assert res.status_code == 401


def test_login_returns_token_for_six_digit_pin(client):
    res = client.post("/api/auth/login",
                      json={"pin": "123456", "device_id": "dev1", "user_id": "u1"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["token"]


def test_protected_route_requires_token(client):
    assert client.get("/api/account").status_code == 401


def test_authenticated_account_request(client, auth_headers):
    res = client.get("/api/account", headers=auth_headers)
    assert res.status_code == 200
    assert "balance" in res.get_json()["account"]


def test_invalid_jwt_rejected(client):
    res = client.get("/api/account", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_ea_endpoint_rejects_without_secret(client):
    assert client.post("/webhook/ea/heartbeat", json={}).status_code == 403


def test_ea_endpoint_rejects_wrong_secret(client):
    res = client.post("/webhook/ea/heartbeat", json={},
                      headers={"X-EA-Secret": "wrong"})
    assert res.status_code == 403


def test_ea_heartbeat_updates_status(client, ea_headers, auth_headers):
    client.post("/webhook/ea/heartbeat",
                headers=ea_headers,
                json={"running": True, "symbol": "XAUUSD", "uptime": 42})
    status = client.get("/api/status", headers=auth_headers).get_json()
    assert status["ea_running"] is True


def test_ea_command_round_trip(client, ea_headers, auth_headers):
    """App sends a command; the next EA heartbeat picks it up exactly once."""
    res = client.post("/api/ea/start", headers=auth_headers)
    assert res.status_code == 200

    first = client.post("/webhook/ea/heartbeat", headers=ea_headers, json={}).get_json()
    assert first["command"]["action"] == "start"

    second = client.post("/webhook/ea/heartbeat", headers=ea_headers, json={}).get_json()
    assert second["command"] is None


def test_invalid_ea_action_rejected(client, auth_headers):
    res = client.post("/api/ea/explode", headers=auth_headers)
    assert res.status_code == 400


def test_trade_lifecycle_end_to_end(client, ea_headers, auth_headers):
    # EA opens a trade
    open_res = client.post("/webhook/ea/trade/open",
                           headers=ea_headers,
                           json={"ticket": "T1", "symbol": "XAUUSD", "type": "BUY",
                                 "lots": 0.1, "entry": 2050, "sl": 2040, "tp": 2080,
                                 "signal": 5})
    assert open_res.status_code == 200

    trades = client.get("/api/trades", headers=auth_headers).get_json()
    assert trades["count"] == 1
    assert trades["trades"][0]["id"] == "T1"

    # EA closes it
    client.post("/webhook/ea/trade/close",
                headers=ea_headers,
                json={"ticket": "T1", "pnl": 42.5, "close_price": 2059})

    open_after = client.get("/api/trades", headers=auth_headers).get_json()
    assert open_after["count"] == 0

    history = client.get("/api/trades/history", headers=auth_headers).get_json()
    assert history["count"] == 1
    assert history["trades"][0]["pnl"] == 42.5

    # Analytics reflect it
    summary = client.get("/api/analytics/summary", headers=auth_headers).get_json()["summary"]
    assert summary["total_trades"] == 1
    assert summary["win_rate"] == 100.0


def test_close_trade_404_for_unknown_id(client, auth_headers):
    res = client.post("/api/trades/missing/close", headers=auth_headers)
    assert res.status_code == 404


def test_alerts_endpoint(client, ea_headers, auth_headers):
    client.post("/webhook/ea/trade/open",
                headers=ea_headers,
                json={"ticket": "A1", "symbol": "XAUUSD", "type": "BUY", "lots": 0.1,
                      "entry": 2050, "signal": 5})
    alerts = client.get("/api/alerts", headers=auth_headers).get_json()["alerts"]
    assert len(alerts) >= 1
    assert "BUY" in alerts[0]["title"]
