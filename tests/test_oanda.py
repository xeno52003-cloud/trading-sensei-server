from unittest.mock import patch

import pytest
import requests

from oanda_client import OandaClient


def _ok(json_body):
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return json_body
    return R()


@pytest.fixture
def client():
    return OandaClient("https://api-fxpractice.oanda.com", "001-001", "tok")


def test_configured_requires_all_three_fields():
    assert OandaClient("", "", "").configured is False
    assert OandaClient("https://x", "001-001", "tok").configured is True


def test_account_summary_normalises_fields(client):
    response = {
        "account": {
            "id": "001-001",
            "balance": "10500.123",
            "unrealizedPL": "87.50",
            "marginUsed": "200.00",
            "marginAvailable": "10300.123",
            "pl": "1200.50",
            "openTradeCount": 2,
            "currency": "USD",
        }
    }
    with patch("oanda_client.requests.get", return_value=_ok(response)) as m:
        summary = client.account_summary()

    assert m.call_args.args[0].endswith("/v3/accounts/001-001/summary")
    assert m.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert summary["balance"] == 10500.12
    assert summary["equity"] == round(10500.123 + 87.50, 2)
    assert summary["open_trades"] == 2
    assert summary["currency"] == "USD"


def test_open_trades_maps_units_to_side_and_lots(client):
    response = {
        "trades": [
            {
                "id": "42",
                "instrument": "XAU_USD",
                "currentUnits": "100",       # positive => BUY, 100/100k = 0.00 lots, near-min
                "price": "2050.5",
                "unrealizedPL": "12.5",
                "openTime": "2026-04-27T09:00:00Z",
                "stopLossOrder": {"price": "2040.0"},
                "takeProfitOrder": {"price": "2080.0"},
            },
            {
                "id": "43",
                "instrument": "EUR_USD",
                "currentUnits": "-10000",    # negative => SELL, 0.10 lots
                "price": "1.085",
                "unrealizedPL": "-3.0",
                "openTime": "2026-04-27T09:05:00Z",
            },
        ]
    }
    with patch("oanda_client.requests.get", return_value=_ok(response)):
        trades = client.open_trades()

    assert len(trades) == 2
    assert trades[0]["type"] == "BUY"
    assert trades[0]["stop_loss"] == 2040.0
    assert trades[0]["take_profit"] == 2080.0
    assert trades[1]["type"] == "SELL"
    assert trades[1]["lots"] == 0.10
    assert trades[1]["status"] == "OPEN"
    assert trades[1]["source"] == "oanda"


def test_pricing_computes_mid_and_spread(client):
    response = {
        "prices": [
            {
                "instrument": "EUR_USD",
                "bids": [{"price": "1.0850"}],
                "asks": [{"price": "1.0852"}],
                "time": "2026-04-27T09:10:00Z",
                "tradeable": True,
            }
        ]
    }
    with patch("oanda_client.requests.get", return_value=_ok(response)) as m:
        prices = client.pricing(["EUR_USD"])

    assert "instruments=EUR_USD" in m.call_args.args[0]
    assert prices[0]["mid"] == round((1.0850 + 1.0852) / 2, 5)
    assert prices[0]["spread"] == round(1.0852 - 1.0850, 5)


def test_pricing_with_no_instruments_skips_request(client):
    with patch("oanda_client.requests.get") as m:
        assert client.pricing([]) == []
        m.assert_not_called()


def test_unconfigured_endpoint_returns_503(server):
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        token = c.post("/api/auth/login", json={"pin": "123456"}).get_json()["token"]
        res = c.get("/api/oanda/account", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 503


def test_oanda_endpoint_handles_upstream_failure(server):
    server.oanda.base_url = "https://api-fxpractice.oanda.com"
    server.oanda.account_id = "001-001"
    server.oanda.token = "tok"
    server.app.config["TESTING"] = True

    with server.app.test_client() as c:
        token = c.post("/api/auth/login", json={"pin": "123456"}).get_json()["token"]
        with patch("oanda_client.requests.get", side_effect=requests.ConnectionError("boom")):
            res = c.get("/api/oanda/account",
                        headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 502
