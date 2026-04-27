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


def test_login_rejects_wrong_pin(client):
    res = client.post("/api/auth/login", json={"pin": "999999"})
    assert res.status_code == 401
    assert res.get_json()["error"] == "Invalid PIN"


def test_login_returns_token_for_correct_pin(client):
    res = client.post("/api/auth/login",
                      json={"pin": "123456", "device_id": "dev1"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["token"]


def test_login_unknown_user_rejected(client):
    res = client.post("/api/auth/login", json={"pin": "123456", "user_id": "nobody"})
    assert res.status_code == 401


def test_lockout_after_repeated_failures(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"pin": "000000"})
    res = client.post("/api/auth/login", json={"pin": "123456"})  # correct PIN now blocked
    assert res.status_code == 401
    assert "Too many" in res.get_json()["error"]


def test_change_pin_flow(client):
    login = client.post("/api/auth/login", json={"pin": "123456"}).get_json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    bad = client.post("/api/auth/change-pin", headers=headers,
                      json={"current_pin": "999999", "new_pin": "654321"})
    assert bad.status_code == 400

    good = client.post("/api/auth/change-pin", headers=headers,
                       json={"current_pin": "123456", "new_pin": "654321"})
    assert good.status_code == 200

    old = client.post("/api/auth/login", json={"pin": "123456"})
    assert old.status_code == 401
    new = client.post("/api/auth/login", json={"pin": "654321"})
    assert new.status_code == 200


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
    """App sends commands; the next EA heartbeat drains them exactly once."""
    client.post("/api/ea/start", headers=auth_headers)

    first = client.post("/webhook/ea/heartbeat", headers=ea_headers, json={}).get_json()
    assert [c["action"] for c in first["commands"]] == ["start"]

    second = client.post("/webhook/ea/heartbeat", headers=ea_headers, json={}).get_json()
    assert second["commands"] == []


def test_trade_actions_queue_for_ea(client, ea_headers, auth_headers):
    """Close, close-all, and modify all enqueue commands the EA can poll for."""
    client.post("/webhook/ea/trade/open",
                headers=ea_headers,
                json={"ticket": "Q1", "symbol": "XAUUSD", "type": "BUY",
                      "lots": 0.1, "entry": 2050, "signal": 5})

    client.post("/api/trades/Q1/close", headers=auth_headers)
    client.put("/api/trades/Q1/modify", headers=auth_headers,
               json={"sl": 2045, "tp": 2080})
    client.post("/api/trades/close-all", headers=auth_headers)

    drained = client.post("/webhook/ea/heartbeat", headers=ea_headers, json={}).get_json()
    actions = [c["action"] for c in drained["commands"]]
    assert actions == ["close_trade", "modify_trade", "close_all"]


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


def test_history_persists_through_a_restart(server, ea_headers, auth_headers):
    """Closed trades survive even though state.trades is wiped."""
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        c.post("/webhook/ea/trade/open",
               headers=ea_headers,
               json={"ticket": "P1", "symbol": "XAUUSD", "type": "BUY",
                     "lots": 0.1, "entry": 2050})
        c.post("/webhook/ea/trade/close",
               headers=ea_headers,
               json={"ticket": "P1", "pnl": 25.0, "close_price": 2055})

        # simulate a restart: blow away the in-memory state but keep history (sqlite)
        server.state.store.set("trades", [])

        history_resp = c.get("/api/trades/history", headers=auth_headers).get_json()
        assert history_resp["count"] == 1
        assert history_resp["trades"][0]["id"] == "P1"

        analytics_resp = c.get("/api/analytics/summary", headers=auth_headers).get_json()
        assert analytics_resp["summary"]["total_trades"] == 1
        assert analytics_resp["summary"]["total_pnl"] == 25.0


def test_websocket_join_rejects_missing_token(server):
    server.app.config["TESTING"] = True
    sio = server.socketio.test_client(server.app)
    sio.emit("join_app", {})
    # Server emits auth_error then disconnects — disconnect is the contract,
    # the client simply can't read after it. Confirm via state side-effect.
    assert sio.is_connected() is False


def test_websocket_join_accepts_valid_token(server):
    server.app.config["TESTING"] = True
    token = server.generate_jwt("test", "dev")
    sio = server.socketio.test_client(server.app)
    sio.emit("join_app", {"token": token})
    received = sio.get_received()
    kinds = {pkt["name"] for pkt in received}
    assert "initial_state" in kinds


def test_alerts_endpoint(client, ea_headers, auth_headers):
    client.post("/webhook/ea/trade/open",
                headers=ea_headers,
                json={"ticket": "A1", "symbol": "XAUUSD", "type": "BUY", "lots": 0.1,
                      "entry": 2050, "signal": 5})
    alerts = client.get("/api/alerts", headers=auth_headers).get_json()["alerts"]
    assert len(alerts) >= 1
    assert "BUY" in alerts[0]["title"]
