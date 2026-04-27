"""
🥷 TRADING SENSEI - Webhook Server

Backend server connecting the MT5 Expert Advisor to the Trading Sensei
mobile/PWA app. REST API + WebSocket + push/Telegram/Discord fan-out.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Optional

import jwt
import requests
from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO, disconnect, emit, join_room, leave_room

import analytics
import oanda_poller
import telegram_bot
from app_state import AppState, new_alert
from circuit_breaker import BreakerConfig, CircuitBreaker
from oanda_client import OandaClient
from state_store import create_state_store
from trade_history import open_history
from users import UserStore, bootstrap_admin


# ============================================
# ⚙️ CONFIGURATION
# ============================================


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    JWT_EXPIRY_HOURS = 24

    REDIS_URL = os.environ.get("REDIS_URL")

    OANDA_API_URL = os.environ.get("OANDA_API_URL", "https://api-fxpractice.oanda.com")
    OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
    OANDA_API_TOKEN = os.environ.get("OANDA_API_TOKEN", "")

    FIREBASE_SERVER_KEY = os.environ.get("FIREBASE_SERVER_KEY", "")
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

    RATE_LIMIT = "100 per minute"
    EA_SECRET = os.environ.get("EA_SECRET", secrets.token_hex(16))

    STARTING_BALANCE = float(os.environ.get("STARTING_BALANCE", "10000"))

    ADMIN_PIN = os.environ.get("ADMIN_PIN", "")

    DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///data/trades.db")

    # Random secret in the Telegram webhook URL — only Telegram and the user know it.
    TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    # Risk circuit breaker — set any threshold to 0 to disable.
    RISK_DAILY_LOSS_PCT = float(os.environ.get("RISK_DAILY_LOSS_PCT", "5.0"))
    RISK_MAX_DRAWDOWN_PCT = float(os.environ.get("RISK_MAX_DRAWDOWN_PCT", "20.0"))
    RISK_MAX_CONSECUTIVE_LOSSES = int(os.environ.get("RISK_MAX_CONSECUTIVE_LOSSES", "5"))


# ============================================
# 🚀 APP SETUP
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("TradingSensei")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config.from_object(Config)

CORS(app, resources={r"/api/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[Config.RATE_LIMIT],
    storage_uri=Config.REDIS_URL or "memory://",
)

_state_store = create_state_store(Config.REDIS_URL)
state = AppState(_state_store)
users = UserStore(_state_store)
bootstrap_admin(users, Config.ADMIN_PIN)

history = open_history(Config.DATABASE_URL)
oanda = OandaClient(Config.OANDA_API_URL, Config.OANDA_ACCOUNT_ID, Config.OANDA_API_TOKEN)

telegram = telegram_bot.TelegramBot(Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID)
telegram_handlers = telegram_bot.build_handlers(state, history, oanda, state.enqueue_ea_command)

breaker = CircuitBreaker(state, history, BreakerConfig(
    daily_loss_pct=Config.RISK_DAILY_LOSS_PCT,
    max_drawdown_pct=Config.RISK_MAX_DRAWDOWN_PCT,
    max_consecutive_losses=Config.RISK_MAX_CONSECUTIVE_LOSSES,
    starting_balance=Config.STARTING_BALANCE,
))


# ============================================
# 🔐 AUTH
# ============================================


def generate_jwt(user_id: str, device_id: str) -> str:
    payload = {
        "user_id": user_id,
        "device_id": device_id,
        "exp": datetime.utcnow() + timedelta(hours=Config.JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing authorization header"}), 401

        payload = verify_jwt(auth_header.split(" ", 1)[1])
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.user_id = payload["user_id"]
        g.device_id = payload["device_id"]
        return f(*args, **kwargs)

    return decorated


def require_ea_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ea_secret = request.headers.get("X-EA-Secret", "")
        if not hmac.compare_digest(ea_secret, Config.EA_SECRET):
            logger.warning("Invalid EA secret from %s", request.remote_addr)
            return jsonify({"error": "Invalid EA secret"}), 403
        return f(*args, **kwargs)

    return decorated


# ============================================
# 🌐 ROOT / DASHBOARD
# ============================================


@app.route("/")
def root():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest")


# ============================================
# 📱 APP API
# ============================================


@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0",
    })


@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", "admin")
    device_id = data.get("device_id", "unknown")
    pin = data.get("pin", "")

    ok, err = users.verify(user_id, pin)
    if not ok:
        logger.info("Failed login: user=%s device=%s reason=%s", user_id, device_id, err)
        return jsonify({"error": err or "Invalid PIN"}), 401

    token = generate_jwt(user_id, device_id)
    logger.info("User %s logged in from device %s", user_id, device_id)
    return jsonify({
        "success": True,
        "token": token,
        "expires_in": Config.JWT_EXPIRY_HOURS * 3600,
    })


@app.route("/api/auth/change-pin", methods=["POST"])
@require_auth
def change_pin():
    data = request.get_json(silent=True) or {}
    ok, err = users.change_pin(g.user_id, data.get("current_pin", ""), data.get("new_pin", ""))
    if not ok:
        return jsonify({"error": err}), 400
    logger.info("User %s changed PIN", g.user_id)
    return jsonify({"success": True})


@app.route("/api/auth/refresh", methods=["POST"])
@require_auth
def refresh_token():
    return jsonify({
        "success": True,
        "token": generate_jwt(g.user_id, g.device_id),
        "expires_in": Config.JWT_EXPIRY_HOURS * 3600,
    })


@app.route("/api/status", methods=["GET"])
@require_auth
def get_status():
    ea = state.get_ea_status()
    ea_connected = bool(ea.get("connected"))
    last_hb = ea.get("last_heartbeat")

    if last_hb:
        try:
            hb_time = datetime.fromisoformat(last_hb)
            if datetime.utcnow() - hb_time > timedelta(seconds=30):
                ea_connected = False
        except ValueError:
            ea_connected = False

    return jsonify({
        "online": True,
        "ea_connected": ea_connected,
        "ea_running": bool(ea.get("running")),
        "last_heartbeat": last_hb,
        "oanda_configured": oanda.configured,
        "connected_devices": len(state.connected_devices),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/account", methods=["GET"])
@require_auth
def get_account():
    return jsonify({
        "success": True,
        "account": state.get_account(),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/ea/status", methods=["GET"])
@require_auth
def get_ea_status():
    return jsonify({
        "success": True,
        "ea": state.get_ea_status(),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/ea/<action>", methods=["POST"])
@require_auth
def control_ea(action: str):
    valid_actions = ["start", "stop", "pause"]
    if action not in valid_actions:
        return jsonify({"error": f"Invalid action. Use: {valid_actions}"}), 400

    command = {
        "action": action,
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": g.user_id,
    }
    state.enqueue_ea_command(command)
    socketio.emit("ea_command", command, room="ea")
    logger.info("EA command: %s by user %s", action, g.user_id)

    return jsonify({"success": True, "message": f"EA {action} command sent", "action": action})


@app.route("/api/trades", methods=["GET"])
@require_auth
def get_trades():
    open_trades = state.get_open_trades()
    return jsonify({
        "success": True,
        "count": len(open_trades),
        "trades": open_trades,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/trades/history", methods=["GET"])
@require_auth
def get_trade_history():
    limit = request.args.get("limit", 50, type=int)
    closed = history.list_closed(limit)
    return jsonify({
        "success": True,
        "count": len(closed),
        "trades": closed,
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/trades/<trade_id>/close", methods=["POST"])
@require_auth
def close_trade(trade_id: str):
    if not state.find_trade(trade_id):
        return jsonify({"error": "Trade not found"}), 404

    command = {
        "action": "close_trade",
        "trade_id": trade_id,
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": g.user_id,
    }
    state.enqueue_ea_command(command)
    socketio.emit("trade_command", command, room="ea")
    logger.info("Close trade command: %s by user %s", trade_id, g.user_id)
    return jsonify({"success": True, "message": f"Close command sent for trade {trade_id}"})


@app.route("/api/trades/close-all", methods=["POST"])
@require_auth
def close_all_trades():
    open_trades = state.get_open_trades()

    command = {
        "action": "close_all",
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": g.user_id,
    }
    state.enqueue_ea_command(command)
    socketio.emit("trade_command", command, room="ea")

    send_alert(
        title="🚨 Emergency Close All",
        message=f"Closing {len(open_trades)} trades",
        alert_type="emergency",
    )
    logger.warning("EMERGENCY CLOSE ALL by user %s", g.user_id)

    return jsonify({
        "success": True,
        "message": f"Close all command sent for {len(open_trades)} trades",
        "count": len(open_trades),
    })


@app.route("/api/trades/<trade_id>/modify", methods=["PUT"])
@require_auth
def modify_trade(trade_id: str):
    data = request.get_json(silent=True) or {}
    if not state.find_trade(trade_id):
        return jsonify({"error": "Trade not found"}), 404

    command = {
        "action": "modify_trade",
        "trade_id": trade_id,
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "timestamp": datetime.utcnow().isoformat(),
        "user_id": g.user_id,
    }
    state.enqueue_ea_command(command)
    socketio.emit("trade_command", command, room="ea")
    return jsonify({"success": True, "message": f"Modify command sent for trade {trade_id}"})


@app.route("/api/analytics/summary", methods=["GET"])
@require_auth
def analytics_summary():
    starting_balance = request.args.get("starting_balance", Config.STARTING_BALANCE, type=float)
    trades = history.all_closed() + state.get_open_trades()
    return jsonify({
        "success": True,
        "summary": analytics.summary(trades, starting_balance),
        "timestamp": datetime.utcnow().isoformat(),
    })


@app.route("/api/oanda/account", methods=["GET"])
@require_auth
def oanda_account():
    if not oanda.configured:
        return jsonify({"error": "OANDA not configured"}), 503
    try:
        return jsonify({"success": True, "account": oanda.account_summary()})
    except requests.RequestException as e:
        logger.error("OANDA account error: %s", e)
        return jsonify({"error": "OANDA request failed"}), 502


@app.route("/api/oanda/trades", methods=["GET"])
@require_auth
def oanda_trades():
    if not oanda.configured:
        return jsonify({"error": "OANDA not configured"}), 503
    try:
        trades = oanda.open_trades()
        return jsonify({"success": True, "count": len(trades), "trades": trades})
    except requests.RequestException as e:
        logger.error("OANDA trades error: %s", e)
        return jsonify({"error": "OANDA request failed"}), 502


@app.route("/api/risk/status", methods=["GET"])
@require_auth
def risk_status():
    return jsonify({"success": True, "risk": breaker.status(state.get_account())})


@app.route("/api/risk/reset", methods=["POST"])
@require_auth
def risk_reset():
    if breaker.reset():
        send_alert("✅ Circuit breaker reset", f"by user {g.user_id}", alert_type="info")
        return jsonify({"success": True, "reset": True})
    return jsonify({"success": True, "reset": False, "message": "Breaker was not tripped"})


@app.route("/api/oanda/import-history", methods=["POST"])
@require_auth
def oanda_import_history():
    if not oanda.configured:
        return jsonify({"error": "OANDA not configured"}), 503
    count = request.args.get("count", 500, type=int)
    try:
        before = history.count()
        for trade in oanda.closed_trades(count):
            history.record_close(trade)
        imported = history.count() - before
        return jsonify({"success": True, "imported": imported, "total": history.count()})
    except requests.RequestException as e:
        logger.error("OANDA import error: %s", e)
        return jsonify({"error": "OANDA request failed"}), 502


@app.route("/api/oanda/pricing", methods=["GET"])
@require_auth
def oanda_pricing():
    if not oanda.configured:
        return jsonify({"error": "OANDA not configured"}), 503
    instruments = request.args.get("instruments", "XAU_USD").split(",")
    try:
        return jsonify({"success": True, "prices": oanda.pricing(instruments)})
    except requests.RequestException as e:
        logger.error("OANDA pricing error: %s", e)
        return jsonify({"error": "OANDA request failed"}), 502


@app.route("/api/alerts", methods=["GET"])
@require_auth
def get_alerts():
    limit = request.args.get("limit", 50, type=int)
    alerts = state.get_alerts(limit)
    return jsonify({"success": True, "count": len(alerts), "alerts": alerts})


@app.route("/api/alerts/mark-read", methods=["POST"])
@require_auth
def mark_alerts_read():
    data = request.get_json(silent=True) or {}
    marked = state.mark_alerts_read(data.get("alert_ids", []))
    return jsonify({"success": True, "marked": marked})


@app.route("/api/device/register", methods=["POST"])
@require_auth
def register_device():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    platform = data.get("platform", "ios")
    if token:
        state.register_device(g.device_id, token, platform)
        logger.info("Device registered: %s (%s)", g.device_id, platform)
    return jsonify({"success": True})


# ============================================
# 🤖 EA WEBHOOKS
# ============================================


@app.route("/webhook/telegram/<secret>", methods=["POST"])
def telegram_webhook(secret: str):
    if not Config.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(secret, Config.TELEGRAM_WEBHOOK_SECRET):
        return jsonify({"error": "Forbidden"}), 403
    update = request.get_json(silent=True) or {}
    telegram_bot.handle_update(telegram, update, telegram_handlers)
    return jsonify({"ok": True})


@app.route("/webhook/ea/heartbeat", methods=["POST"])
@require_ea_auth
def ea_heartbeat():
    data = request.get_json(silent=True) or {}
    state.update_ea_status(
        connected=True,
        running=bool(data.get("running", False)),
        symbol=data.get("symbol", "XAUUSD"),
        timeframe=data.get("timeframe", "M15"),
        last_heartbeat=datetime.utcnow().isoformat(),
        uptime=data.get("uptime", 0),
        version=data.get("version", "2.0.0"),
    )
    return jsonify({"success": True, "commands": state.drain_ea_commands()})


@app.route("/webhook/ea/account", methods=["POST"])
@require_ea_auth
def ea_account_update():
    data = request.get_json(silent=True) or {}
    account = state.update_account(
        balance=data.get("balance", 0),
        equity=data.get("equity", 0),
        margin_used=data.get("margin_used", 0),
        margin_available=data.get("margin_available", 0),
        unrealized_pnl=data.get("unrealized_pnl", 0),
        total_pnl=data.get("total_pnl", 0),
        open_trades=data.get("open_trades", 0),
    )
    socketio.emit("account_update", account, room="app")
    _evaluate_breaker(account)
    return jsonify({"success": True})


def _evaluate_breaker(account: dict[str, Any]) -> None:
    reason = breaker.evaluate(account)
    if reason and not breaker.is_tripped():
        breaker.trip(
            reason,
            enqueue=state.enqueue_ea_command,
            alert=lambda title, msg: send_alert(title, msg, alert_type="emergency"),
        )


@app.route("/webhook/ea/trade/open", methods=["POST"])
@require_ea_auth
def ea_trade_opened():
    data = request.get_json(silent=True) or {}
    trade = {
        "id": str(data.get("ticket") or int(time.time() * 1000)),
        "symbol": data.get("symbol", "XAUUSD"),
        "type": data.get("type", "BUY"),
        "lots": data.get("lots", 0.01),
        "entry_price": data.get("entry", 0),
        "current_price": data.get("current", 0),
        "stop_loss": data.get("sl", 0),
        "take_profit": data.get("tp", 0),
        "pnl": data.get("pnl", 0),
        "pips": data.get("pips", 0),
        "open_time": data.get("open_time", datetime.utcnow().isoformat()),
        "status": "OPEN",
        "signal_strength": data.get("signal", 5),
    }
    state.add_trade(trade)

    alert = new_alert(
        title=f"🟢 {trade['type']} {trade['symbol']}",
        message=f"Entry: {trade['entry_price']} | Lots: {trade['lots']} | Signal: {trade['signal_strength']}/7",
        alert_type="buy" if trade["type"] == "BUY" else "sell",
    )
    state.add_alert(alert)
    socketio.emit("new_alert", alert, room="app")
    socketio.emit("trade_opened", trade, room="app")

    send_push_notification(
        title=f"🥷 New Trade: {trade['type']} {trade['symbol']}",
        body=f"Entry: {trade['entry_price']} | Signal: {trade['signal_strength']}/7",
    )
    send_telegram_alert(
        f"🥷 *NEW TRADE*\n\n"
        f"📊 {trade['type']} {trade['symbol']}\n"
        f"💰 Lots: {trade['lots']}\n"
        f"📍 Entry: {trade['entry_price']}\n"
        f"🛑 SL: {trade['stop_loss']}\n"
        f"🎯 TP: {trade['take_profit']}\n"
        f"⚡ Signal: {trade['signal_strength']}/7"
    )

    logger.info("Trade opened: %s %s @ %s", trade["type"], trade["symbol"], trade["entry_price"])
    return jsonify({"success": True, "trade_id": trade["id"]})


@app.route("/webhook/ea/trade/close", methods=["POST"])
@require_ea_auth
def ea_trade_closed():
    data = request.get_json(silent=True) or {}
    trade_id = str(data.get("ticket", ""))

    pnl = float(data.get("pnl", 0) or 0)
    close_price = data.get("close_price", 0)

    trade = state.update_trade(
        trade_id,
        status="CLOSED",
        pnl=pnl,
        close_price=close_price,
        close_time=datetime.utcnow().isoformat(),
    )
    if not trade:
        return jsonify({"success": False, "error": "Trade not found"}), 404

    history.record_close(trade)
    _evaluate_breaker(state.get_account())

    is_profit = pnl >= 0
    alert = new_alert(
        title=f"{'✅' if is_profit else '❌'} Closed {'+' if is_profit else ''}${pnl:.2f}",
        message=f"{trade['type']} {trade['symbol']} @ {close_price}",
        alert_type="profit" if is_profit else "loss",
    )
    state.add_alert(alert)
    socketio.emit("new_alert", alert, room="app")
    socketio.emit("trade_closed", trade, room="app")

    emoji = "🎉" if is_profit else "😤"
    send_push_notification(
        title=f"{emoji} Trade Closed: {'+' if is_profit else ''}${pnl:.2f}",
        body=f"{trade['symbol']} closed at {close_price}",
    )
    send_telegram_alert(
        f"{emoji} *TRADE CLOSED*\n\n"
        f"📊 {trade['type']} {trade['symbol']}\n"
        f"💰 P&L: {'+' if is_profit else ''}${pnl:.2f}\n"
        f"📍 Close: {close_price}"
    )

    logger.info("Trade closed: %s | P&L: $%.2f", trade_id, pnl)
    return jsonify({"success": True})


@app.route("/webhook/ea/trade/update", methods=["POST"])
@require_ea_auth
def ea_trade_update():
    data = request.get_json(silent=True) or {}
    trade_id = str(data.get("ticket", ""))

    trade = state.find_trade(trade_id)
    if not trade or trade.get("status") != "OPEN":
        return jsonify({"success": False, "error": "Open trade not found"}), 404

    trade = state.update_trade(
        trade_id,
        current_price=data.get("current", trade["current_price"]),
        pnl=data.get("pnl", trade["pnl"]),
        pips=data.get("pips", trade["pips"]),
    )
    socketio.emit("trade_update", trade, room="app")
    return jsonify({"success": True})


@app.route("/webhook/ea/signal", methods=["POST"])
@require_ea_auth
def ea_signal():
    data = request.get_json(silent=True) or {}
    last_signal = {
        "type": data.get("type"),
        "symbol": data.get("symbol"),
        "strength": data.get("strength"),
        "timestamp": datetime.utcnow().isoformat(),
    }
    state.update_ea_status(last_signal=last_signal)
    socketio.emit("new_signal", last_signal, room="app")
    return jsonify({"success": True})


# ============================================
# 🔔 NOTIFICATIONS
# ============================================


def send_alert(title: str, message: str, alert_type: str = "info") -> dict[str, Any]:
    alert = new_alert(title, message, alert_type)
    state.add_alert(alert)
    socketio.emit("new_alert", alert, room="app")
    send_push_notification(title, message)
    send_telegram_alert(f"*{title}*\n{message}")
    send_discord_alert(title, message)
    return alert


def send_push_notification(title: str, body: str) -> None:
    if not Config.FIREBASE_SERVER_KEY:
        return

    for device_id, info in state.get_device_tokens().items():
        try:
            response = requests.post(
                "https://fcm.googleapis.com/fcm/send",
                headers={
                    "Authorization": f"key={Config.FIREBASE_SERVER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": info["token"],
                    "notification": {"title": title, "body": body, "sound": "default"},
                    "data": {"type": "trade_alert", "timestamp": datetime.utcnow().isoformat()},
                },
                timeout=5,
            )
            if response.status_code != 200:
                logger.error("FCM error for %s: %s", device_id, response.text)
        except requests.RequestException as e:
            logger.error("Push notification error: %s", e)


def send_telegram_alert(message: str) -> None:
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": Config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=5,
        )
    except requests.RequestException as e:
        logger.error("Telegram error: %s", e)


def send_discord_alert(title: str, message: str) -> None:
    if not Config.DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            Config.DISCORD_WEBHOOK_URL,
            json={
                "embeds": [{
                    "title": title,
                    "description": message,
                    "color": 0x6366F1,
                    "timestamp": datetime.utcnow().isoformat(),
                    "footer": {"text": "🥷 Trading Sensei"},
                }]
            },
            timeout=5,
        )
    except requests.RequestException as e:
        logger.error("Discord error: %s", e)


# ============================================
# 🔌 WEBSOCKET
# ============================================


@socketio.on("connect")
def handle_connect():
    logger.info("Client connected: %s", request.sid)


@socketio.on("disconnect")
def handle_disconnect():
    state.connected_devices.discard(request.sid)
    logger.info("Client disconnected: %s", request.sid)


@socketio.on("join_app")
def handle_join_app(data):
    token = (data or {}).get("token", "")
    payload = verify_jwt(token)
    if not payload:
        logger.warning("Rejected app socket %s: invalid token", request.sid)
        emit("auth_error", {"message": "Invalid or expired token"})
        disconnect()
        return

    join_room("app")
    state.connected_devices.add(request.sid)
    emit("initial_state", {
        "account": state.get_account(),
        "ea_status": state.get_ea_status(),
        "trades": state.get_open_trades(),
        "alerts": state.get_alerts(10),
    })
    logger.info("App joined: %s (user=%s)", request.sid, payload["user_id"])


@socketio.on("join_ea")
def handle_join_ea(data):
    if not hmac.compare_digest(data.get("secret", ""), Config.EA_SECRET):
        emit("error", {"message": "Invalid EA secret"})
        return
    join_room("ea")
    socketio.emit("ea_connected", {"connected": True}, room="app")
    logger.info("EA connected: %s", request.sid)


@socketio.on("leave_ea")
def handle_leave_ea():
    leave_room("ea")
    state.update_ea_status(connected=False)
    socketio.emit("ea_disconnected", {"connected": False}, room="app")
    logger.info("EA disconnected: %s", request.sid)


# ============================================
# 🚀 MAIN
# ============================================


def _socket_emit(event: str, payload: Any) -> None:
    socketio.emit(event, payload, room="app")


if os.environ.get("DISABLE_OANDA_POLLER") != "1":
    oanda_poller.start(oanda, state, _socket_emit)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"""
    🥷 ====================================
       TRADING SENSEI WEBHOOK SERVER
    ====================================

    Server starting on port {port}

    🌐 Dashboard:   http://localhost:{port}/
    📱 App API:     http://localhost:{port}/api/
    🤖 EA Webhook:  http://localhost:{port}/webhook/ea/
    🔌 WebSocket:   ws://localhost:{port}

    🥷 Trade Like a Ninja!
    ====================================
    """)
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=os.environ.get("DEBUG", "false").lower() == "true",
        allow_unsafe_werkzeug=True,
    )
