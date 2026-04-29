# MT5 ↔ Trading Sensei Server Bridge

`TradingSensei_Webhook.mqh` is a drop-in module that wires any MT5 EA to the
Trading Sensei server. The EA reports trades and account state; the server
returns queued commands (start/stop/pause/close/modify) on every heartbeat.

## What you get

| EA → Server (POST)              | When                        |
|---------------------------------|-----------------------------|
| `/webhook/ea/heartbeat`         | every `InpWebhookHeartbeatSec` |
| `/webhook/ea/account`           | every 30 s                  |
| `/webhook/ea/trade/open`        | trade opens                 |
| `/webhook/ea/trade/close`       | trade closes                |
| `/webhook/ea/trade/update`      | price/PnL ticks (optional)  |
| `/webhook/ea/signal`            | new signal, no trade taken  |

| Server → EA (heartbeat response) | Effect                                |
|----------------------------------|---------------------------------------|
| `start` / `stop` / `pause`       | flips `g_webhook_ea_running`          |
| `close_trade` (with `trade_id`)  | closes the matching position          |
| `close_all`                      | closes all positions for the magic    |
| `modify_trade` (`sl`, `tp`)      | modifies the matching position        |

All requests carry an `X-EA-Secret` header. The server rejects mismatches
(see `EA_SECRET` in your server `.env`).

## Install

1. Copy `TradingSensei_Webhook.mqh` into MT5's `MQL5\Include\` folder.
   *In MT5: File → Open Data Folder → MQL5 → Include.*
2. **Allow the server URL.** MT5 → Tools → Options → Expert Advisors →
   tick *Allow WebRequest for listed URL* → add your full server origin
   (e.g. `https://your-app.railway.app`). Without this, every `WebRequest`
   call returns -1 with error 4060.
3. Recompile your EA (F7).

## Wire it into your EA

Three lines plus a transaction handler:

```mql5
#include <TradingSensei_Webhook.mqh>

int OnInit()
{
    WebhookInit();               // call once
    return INIT_SUCCEEDED;
}

void OnTick()
{
    WebhookHeartbeat();          // drains commands, throttled internally
    WebhookAccountUpdate();      // throttled to 30 s

    if(!g_webhook_ea_running) return;   // honour remote stop/pause
    // ... your strategy ...
}

void OnTradeTransaction(const MqlTradeTransaction &t, const MqlTradeRequest &,
                        const MqlTradeResult &)
{
    // Report opens/closes — see TradingSensei_Example.mq5 for the full body
}
```

`TradingSensei_Example.mq5` in this folder is a complete, compilable
template — copy it, paste your existing signal logic into the marked block.

## Patching `OANDA_AI_Trading_EA*.mq5`

If you'd rather instrument your existing EAs, add **just** these calls:

```mql5
// at the top of the file, after the other #include lines:
#include <TradingSensei_Webhook.mqh>

// inside OnInit() — anywhere after trade.SetExpertMagicNumber(...):
WebhookInit();

// inside OnTick(), at the very top:
WebhookHeartbeat();
WebhookAccountUpdate();
if(!g_webhook_ea_running) return;   // optional: respect remote pause

// inside OpenBuyTrade() right after `trade.Buy(...)` succeeds:
WebhookTradeOpened(trade.ResultDeal(), "BUY", lotSize, price, sl, tp, signalStrength);

// same in OpenSellTrade() with "SELL"

// inside the PRO EA's OnTrade() handler, where you already detect closures:
WebhookTradeClosed(ticket, profit, price);
```

`WebhookInit()` sets a `CTrade _ts_trade` instance with
`InpWebhookMagicNumber`. Use the **same** magic number you pass to your EA's
own `trade.SetExpertMagicNumber(...)` so server-issued `close_all` only
touches positions opened by this EA.

## Sanity check

With the EA attached to a chart and `InpWebhookEnabled = true`:

```bash
# tail the server log
docker logs -f trading-sensei

# you should see heartbeats arriving every 5 s:
INFO - 127.0.0.1 "POST /webhook/ea/heartbeat HTTP/1.1" 200
```

In the dashboard at `/`, the connection dot turns green and the EA section
of the home page reports "running".

## Troubleshooting

| Symptom                                  | Fix                                                       |
|------------------------------------------|-----------------------------------------------------------|
| `Webhook error 4060`                     | URL not in MT5's WebRequest allowlist (step 2 above)      |
| Server logs `Invalid EA secret from ...` | `InpWebhookSecret` ≠ server `EA_SECRET`                   |
| Commands fire repeatedly                 | Two EA instances pointing at the same server (drain race) |
| Dashboard shows EA disconnected          | Heartbeats older than 30 s — check the EA log for errors  |
