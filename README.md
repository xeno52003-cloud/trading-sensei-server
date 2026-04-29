# 🥷 Trading Sensei

Backend + dashboard for the OANDA AI Trading EA. PIN-protected web app
that talks to your MT5 EA over webhooks, persists trade history, runs a
risk circuit breaker, and pushes alerts to Telegram / Discord / mobile.

## See it in action (no install)

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2Fxeno52003-cloud%2Ftrading-sensei-server&envs=ADMIN_PIN%2CSECRET_KEY%2CJWT_SECRET%2CEA_SECRET%2CDEMO&optionalADMIN_PIN=Six-digit+PIN+you%27ll+use+to+log+in&optionalSECRET_KEY=Run+%60python+-c+%22import+secrets%3Bprint%28secrets.token_hex%2832%29%29%22%60&optionalJWT_SECRET=Same+command+as+SECRET_KEY&optionalEA_SECRET=Run+%60python+-c+%22import+secrets%3Bprint%28secrets.token_hex%2816%29%29%22%60&optionalDEMO=Set+to+1+to+seed+demo+data+on+first+boot)

Click the button, fill in:
- `ADMIN_PIN` — six digits, your login PIN
- `SECRET_KEY` / `JWT_SECRET` — any long random strings
  (`python -c "import secrets;print(secrets.token_hex(32))"`)
- `EA_SECRET` — random string your MT5 EA will use as `X-EA-Secret`
- `DEMO=1` — seeds the dashboard with fake trades on first boot

Railway gives you an HTTPS URL in ~3 minutes. Open it, enter your PIN,
done.

## Or run locally

```bash
git clone https://github.com/xeno52003-cloud/trading-sensei-server
cd trading-sensei-server
git checkout claude/trading-app-features-OQyjm
pip install -r requirements.txt

SECRET_KEY=x JWT_SECRET=y EA_SECRET=z ADMIN_PIN=123456 DEMO=1 \
  python webhook_server.py
```

Open http://localhost:5000 → PIN `123456`.

## What's inside

| Component                      | Where                                           |
|--------------------------------|-------------------------------------------------|
| REST + Socket.IO server        | `webhook_server.py`                             |
| PWA dashboard                  | `static/index.html`, `static/app.js`            |
| Domain logic                   | `app_state.py`, `analytics.py`                  |
| Persistence (SQLite + Redis)   | `state_store.py`, `trade_history.py`            |
| Auth (bcrypt PINs)             | `users.py`                                      |
| OANDA REST fallback + import   | `oanda_client.py`, `oanda_poller.py`            |
| Risk circuit breaker           | `circuit_breaker.py`                            |
| EA-disconnect watchdog         | `ea_watchdog.py`                                |
| Two-way Telegram bot           | `telegram_bot.py`                               |
| MT5 EA bridge (drop-in)        | `mt5/TradingSensei_Webhook.mqh`                 |
| React Native starter           | `mobile/api.ts`, `mobile/App.tsx`               |
| Demo seeder (DEMO=1)           | `demo_seed.py`                                  |
| CI                             | `.github/workflows/ci.yml`                      |
| Tests (81 passing)             | `tests/`                                        |

## Configuration

Copy `.env.example` to `.env` and fill in what you need. Only the four
secrets and `ADMIN_PIN` are required; everything else is optional and
the server degrades gracefully (no Telegram token = no Telegram, no
OANDA token = no fallback, etc.).

## Connecting your MT5 EA

See [`mt5/README.md`](mt5/README.md). You add three lines to your EA,
whitelist your server URL in MT5's WebRequest settings, and the
dashboard goes live.
