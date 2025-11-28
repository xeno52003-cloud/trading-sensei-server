"""
🥷 TRADING SENSEI - Webhook Server
====================================

Complete backend server for Trading Sensei mobile app.
Connects MT5 Expert Advisor to iOS/Android app.

Features:
- REST API for app communication
- WebSocket for real-time updates
- Push notification delivery
- Trade management
- Account monitoring
- Security & authentication

Deployment Options:
- Railway.app (recommended, free tier)
- Render.com
- Heroku
- DigitalOcean
- AWS/GCP/Azure

Author: Master Xeno Trading Systems
Version: 2.0.0
"""

import os
import json
import time
import hmac
import hashlib
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import jwt
import redis
import requests
from dataclasses import dataclass, asdict
from enum import Enum

# ============================================
# ⚙️ CONFIGURATION
# ============================================

class Config:
    """Server configuration"""
    
    # Server
    SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_hex(32))
    JWT_SECRET = os.environ.get('JWT_SECRET', secrets.token_hex(32))
    JWT_EXPIRY_HOURS = 24
    
    # Redis (for session/state management)
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    # OANDA API
    OANDA_API_URL = os.environ.get('OANDA_API_URL', 'https://api-fxpractice.oanda.com')
    OANDA_ACCOUNT_ID = os.environ.get('OANDA_ACCOUNT_ID', '')
    OANDA_API_TOKEN = os.environ.get('OANDA_API_TOKEN', '')
    
    # Push Notifications
    FIREBASE_SERVER_KEY = os.environ.get('FIREBASE_SERVER_KEY', '')
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    # Discord Webhook
    DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')
    
    # Security
    API_KEY_HEADER = 'X-API-Key'
    ALLOWED_IPS = os.environ.get('ALLOWED_IPS', '').split(',')
    RATE_LIMIT = "100 per minute"
    
    # EA Communication
    EA_SECRET = os.environ.get('EA_SECRET', secrets.token_hex(16))


# ============================================
# 📦 DATA MODELS
# ============================================

class TradeType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    PENDING = "PENDING"


@dataclass
class Trade:
    id: str
    symbol: str
    type: TradeType
    lots: float
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    pnl: float
    pips: float
    open_time: str
    status: TradeStatus
    signal_strength: int = 5
    
    def to_dict(self):
        return {
            **asdict(self),
            'type': self.type.value,
            'status': self.status.value,
        }


@dataclass
class AccountInfo:
    balance: float
    equity: float
    margin_used: float
    margin_available: float
    unrealized_pnl: float
    total_pnl: float
    open_trades: int


@dataclass
class EAStatus:
    running: bool
    symbol: str
    timeframe: str
    last_signal: str
    last_trade_time: str
    version: str
    uptime: int


# ============================================
# 🚀 FLASK APP SETUP
# ============================================

app = Flask(__name__)
app.config.from_object(Config)

CORS(app, resources={r"/api/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[Config.RATE_LIMIT]
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('TradingSensei')

# In-memory state (use Redis in production)
state = {
    'ea_status': {
        'running': False,
        'symbol': 'XAUUSD',
        'timeframe': 'M15',
        'last_signal': None,
        'last_trade_time': None,
        'version': '2.0.0',
        'uptime': 0,
        'connected': False,
        'last_heartbeat': None,
    },
    'account': {
        'balance': 10000.00,
        'equity': 10000.00,
        'margin_used': 0,
        'margin_available': 10000.00,
        'unrealized_pnl': 0,
        'total_pnl': 0,
        'open_trades': 0,
    },
    'trades': [],
    'alerts': [],
    'connected_devices': set(),
    'device_tokens': {},
}


# ============================================
# 🔐 AUTHENTICATION
# ============================================

def generate_jwt(user_id: str, device_id: str) -> str:
    """Generate JWT token for authenticated user"""
    payload = {
        'user_id': user_id,
        'device_id': device_id,
        'exp': datetime.utcnow() + timedelta(hours=Config.JWT_EXPIRY_HOURS),
        'iat': datetime.utcnow(),
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm='HS256')


def verify_jwt(token: str) -> Optional[Dict]:
    """Verify JWT token"""
    try:
        return jwt.decode(token, Config.JWT_SECRET, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing authorization header'}), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_jwt(token)
        
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        g.user_id = payload['user_id']
        g.device_id = payload['device_id']
        
        return f(*args, **kwargs)
    return decorated


def require_ea_auth(f):
    """Decorator to require EA authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        ea_secret = request.headers.get('X-EA-Secret', '')
        
        if not hmac.compare_digest(ea_secret, Config.EA_SECRET):
            logger.warning(f"Invalid EA secret from {request.remote_addr}")
            return jsonify({'error': 'Invalid EA secret'}), 403
        
        return f(*args, **kwargs)
    return decorated


# ============================================
# 📱 APP API ENDPOINTS
# ============================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '2.0.0',
    })


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    """Authenticate user and return JWT token"""
    data = request.get_json()
    
    # In production, validate against database
    user_id = data.get('user_id', 'default')
    device_id = data.get('device_id', 'unknown')
    pin = data.get('pin', '')
    
    # Demo: accept any 6-digit PIN
    if len(pin) != 6 or not pin.isdigit():
        return jsonify({'error': 'Invalid PIN'}), 401
    
    token = generate_jwt(user_id, device_id)
    
    logger.info(f"User {user_id} logged in from device {device_id}")
    
    return jsonify({
        'success': True,
        'token': token,
        'expires_in': Config.JWT_EXPIRY_HOURS * 3600,
    })


@app.route('/api/auth/refresh', methods=['POST'])
@require_auth
def refresh_token():
    """Refresh JWT token"""
    token = generate_jwt(g.user_id, g.device_id)
    return jsonify({
        'success': True,
        'token': token,
        'expires_in': Config.JWT_EXPIRY_HOURS * 3600,
    })


@app.route('/api/status', methods=['GET'])
@require_auth
def get_status():
    """Get overall system status"""
    ea_connected = state['ea_status']['connected']
    last_hb = state['ea_status']['last_heartbeat']
    
    # Check if EA heartbeat is stale (>30 seconds)
    if last_hb:
        hb_time = datetime.fromisoformat(last_hb)
        if datetime.utcnow() - hb_time > timedelta(seconds=30):
            ea_connected = False
    
    return jsonify({
        'online': True,
        'ea_connected': ea_connected,
        'ea_running': state['ea_status']['running'],
        'last_heartbeat': last_hb,
        'connected_devices': len(state['connected_devices']),
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/api/account', methods=['GET'])
@require_auth
def get_account():
    """Get account information"""
    return jsonify({
        'success': True,
        'account': state['account'],
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/api/ea/status', methods=['GET'])
@require_auth
def get_ea_status():
    """Get EA status"""
    return jsonify({
        'success': True,
        'ea': state['ea_status'],
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/api/ea/<action>', methods=['POST'])
@require_auth
def control_ea(action: str):
    """Control EA (start/stop/pause)"""
    valid_actions = ['start', 'stop', 'pause']
    
    if action not in valid_actions:
        return jsonify({'error': f'Invalid action. Use: {valid_actions}'}), 400
    
    # Store command for EA to pick up
    state['ea_command'] = {
        'action': action,
        'timestamp': datetime.utcnow().isoformat(),
        'user_id': g.user_id,
    }
    
    # Emit to connected EAs via WebSocket
    socketio.emit('ea_command', state['ea_command'], room='ea')
    
    logger.info(f"EA command: {action} by user {g.user_id}")
    
    return jsonify({
        'success': True,
        'message': f'EA {action} command sent',
        'action': action,
    })


@app.route('/api/trades', methods=['GET'])
@require_auth
def get_trades():
    """Get open trades"""
    open_trades = [t for t in state['trades'] if t.get('status') == 'OPEN']
    
    return jsonify({
        'success': True,
        'count': len(open_trades),
        'trades': open_trades,
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/api/trades/history', methods=['GET'])
@require_auth
def get_trade_history():
    """Get trade history"""
    limit = request.args.get('limit', 50, type=int)
    closed_trades = [t for t in state['trades'] if t.get('status') == 'CLOSED']
    
    return jsonify({
        'success': True,
        'count': len(closed_trades),
        'trades': closed_trades[-limit:],
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/api/trades/<trade_id>/close', methods=['POST'])
@require_auth
def close_trade(trade_id: str):
    """Close a specific trade"""
    trade = next((t for t in state['trades'] if t['id'] == trade_id), None)
    
    if not trade:
        return jsonify({'error': 'Trade not found'}), 404
    
    # Send close command to EA
    close_command = {
        'action': 'close_trade',
        'trade_id': trade_id,
        'timestamp': datetime.utcnow().isoformat(),
        'user_id': g.user_id,
    }
    
    socketio.emit('trade_command', close_command, room='ea')
    
    logger.info(f"Close trade command: {trade_id} by user {g.user_id}")
    
    return jsonify({
        'success': True,
        'message': f'Close command sent for trade {trade_id}',
    })


@app.route('/api/trades/close-all', methods=['POST'])
@require_auth
def close_all_trades():
    """Emergency close all trades"""
    open_trades = [t for t in state['trades'] if t.get('status') == 'OPEN']
    
    # Send close all command to EA
    close_command = {
        'action': 'close_all',
        'timestamp': datetime.utcnow().isoformat(),
        'user_id': g.user_id,
    }
    
    socketio.emit('trade_command', close_command, room='ea')
    
    # Send alert
    send_alert(
        title='🚨 Emergency Close All',
        message=f'Closing {len(open_trades)} trades',
        alert_type='emergency'
    )
    
    logger.warning(f"EMERGENCY CLOSE ALL by user {g.user_id}")
    
    return jsonify({
        'success': True,
        'message': f'Close all command sent for {len(open_trades)} trades',
        'count': len(open_trades),
    })


@app.route('/api/trades/<trade_id>/modify', methods=['PUT'])
@require_auth
def modify_trade(trade_id: str):
    """Modify trade SL/TP"""
    data = request.get_json()
    
    trade = next((t for t in state['trades'] if t['id'] == trade_id), None)
    
    if not trade:
        return jsonify({'error': 'Trade not found'}), 404
    
    modify_command = {
        'action': 'modify_trade',
        'trade_id': trade_id,
        'sl': data.get('sl'),
        'tp': data.get('tp'),
        'timestamp': datetime.utcnow().isoformat(),
        'user_id': g.user_id,
    }
    
    socketio.emit('trade_command', modify_command, room='ea')
    
    return jsonify({
        'success': True,
        'message': f'Modify command sent for trade {trade_id}',
    })


@app.route('/api/alerts', methods=['GET'])
@require_auth
def get_alerts():
    """Get alerts"""
    limit = request.args.get('limit', 50, type=int)
    
    return jsonify({
        'success': True,
        'count': len(state['alerts']),
        'alerts': state['alerts'][-limit:],
    })


@app.route('/api/alerts/mark-read', methods=['POST'])
@require_auth
def mark_alerts_read():
    """Mark alerts as read"""
    data = request.get_json()
    alert_ids = data.get('alert_ids', [])
    
    for alert in state['alerts']:
        if alert['id'] in alert_ids:
            alert['read'] = True
    
    return jsonify({'success': True})


@app.route('/api/device/register', methods=['POST'])
@require_auth
def register_device():
    """Register device for push notifications"""
    data = request.get_json()
    
    device_token = data.get('token')
    platform = data.get('platform', 'ios')
    
    if device_token:
        state['device_tokens'][g.device_id] = {
            'token': device_token,
            'platform': platform,
            'registered_at': datetime.utcnow().isoformat(),
        }
        
        logger.info(f"Device registered: {g.device_id} ({platform})")
    
    return jsonify({'success': True})


# ============================================
# 🤖 EA WEBHOOK ENDPOINTS
# ============================================

@app.route('/webhook/ea/heartbeat', methods=['POST'])
@require_ea_auth
def ea_heartbeat():
    """EA heartbeat - called every 5 seconds"""
    data = request.get_json()
    
    state['ea_status'].update({
        'connected': True,
        'running': data.get('running', False),
        'symbol': data.get('symbol', 'XAUUSD'),
        'timeframe': data.get('timeframe', 'M15'),
        'last_heartbeat': datetime.utcnow().isoformat(),
        'uptime': data.get('uptime', 0),
        'version': data.get('version', '2.0.0'),
    })
    
    # Check for pending commands
    command = state.get('ea_command')
    state['ea_command'] = None
    
    return jsonify({
        'success': True,
        'command': command,
    })


@app.route('/webhook/ea/account', methods=['POST'])
@require_ea_auth
def ea_account_update():
    """EA account update"""
    data = request.get_json()
    
    state['account'].update({
        'balance': data.get('balance', 0),
        'equity': data.get('equity', 0),
        'margin_used': data.get('margin_used', 0),
        'margin_available': data.get('margin_available', 0),
        'unrealized_pnl': data.get('unrealized_pnl', 0),
        'total_pnl': data.get('total_pnl', 0),
        'open_trades': data.get('open_trades', 0),
    })
    
    # Broadcast to connected apps
    socketio.emit('account_update', state['account'], room='app')
    
    return jsonify({'success': True})


@app.route('/webhook/ea/trade/open', methods=['POST'])
@require_ea_auth
def ea_trade_opened():
    """EA reports new trade opened"""
    data = request.get_json()
    
    trade = {
        'id': data.get('ticket', str(int(time.time() * 1000))),
        'symbol': data.get('symbol', 'XAUUSD'),
        'type': data.get('type', 'BUY'),
        'lots': data.get('lots', 0.01),
        'entry_price': data.get('entry', 0),
        'current_price': data.get('current', 0),
        'stop_loss': data.get('sl', 0),
        'take_profit': data.get('tp', 0),
        'pnl': data.get('pnl', 0),
        'pips': data.get('pips', 0),
        'open_time': data.get('open_time', datetime.utcnow().isoformat()),
        'status': 'OPEN',
        'signal_strength': data.get('signal', 5),
    }
    
    state['trades'].append(trade)
    
    # Create alert
    alert = create_alert(
        title=f"🟢 {trade['type']} {trade['symbol']}",
        message=f"Entry: {trade['entry_price']} | Lots: {trade['lots']} | Signal: {trade['signal_strength']}/7",
        alert_type='buy' if trade['type'] == 'BUY' else 'sell'
    )
    
    # Broadcast
    socketio.emit('trade_opened', trade, room='app')
    
    # Push notifications
    send_push_notification(
        title=f"🥷 New Trade: {trade['type']} {trade['symbol']}",
        body=f"Entry: {trade['entry_price']} | Signal: {trade['signal_strength']}/7"
    )
    
    # Telegram
    send_telegram_alert(
        f"🥷 *NEW TRADE*\n\n"
        f"📊 {trade['type']} {trade['symbol']}\n"
        f"💰 Lots: {trade['lots']}\n"
        f"📍 Entry: {trade['entry_price']}\n"
        f"🛑 SL: {trade['stop_loss']}\n"
        f"🎯 TP: {trade['take_profit']}\n"
        f"⚡ Signal: {trade['signal_strength']}/7"
    )
    
    logger.info(f"Trade opened: {trade['type']} {trade['symbol']} @ {trade['entry_price']}")
    
    return jsonify({'success': True, 'trade_id': trade['id']})


@app.route('/webhook/ea/trade/close', methods=['POST'])
@require_ea_auth
def ea_trade_closed():
    """EA reports trade closed"""
    data = request.get_json()
    
    trade_id = data.get('ticket', '')
    
    # Update trade in state
    for trade in state['trades']:
        if trade['id'] == trade_id:
            trade['status'] = 'CLOSED'
            trade['pnl'] = data.get('pnl', 0)
            trade['close_price'] = data.get('close_price', 0)
            trade['close_time'] = datetime.utcnow().isoformat()
            
            # Create alert
            is_profit = trade['pnl'] >= 0
            alert = create_alert(
                title=f"{'✅' if is_profit else '❌'} Closed {'+' if is_profit else ''}${trade['pnl']:.2f}",
                message=f"{trade['type']} {trade['symbol']} @ {trade['close_price']}",
                alert_type='profit' if is_profit else 'loss'
            )
            
            # Broadcast
            socketio.emit('trade_closed', trade, room='app')
            
            # Push notification
            emoji = '🎉' if is_profit else '😤'
            send_push_notification(
                title=f"{emoji} Trade Closed: {'+' if is_profit else ''}${trade['pnl']:.2f}",
                body=f"{trade['symbol']} closed at {trade['close_price']}"
            )
            
            # Telegram
            send_telegram_alert(
                f"{'🎉' if is_profit else '😤'} *TRADE CLOSED*\n\n"
                f"📊 {trade['type']} {trade['symbol']}\n"
                f"💰 P&L: {'+' if is_profit else ''}${trade['pnl']:.2f}\n"
                f"📍 Close: {trade['close_price']}"
            )
            
            logger.info(f"Trade closed: {trade_id} | P&L: ${trade['pnl']:.2f}")
            break
    
    return jsonify({'success': True})


@app.route('/webhook/ea/trade/update', methods=['POST'])
@require_ea_auth
def ea_trade_update():
    """EA reports trade update (price change)"""
    data = request.get_json()
    
    trade_id = data.get('ticket', '')
    
    for trade in state['trades']:
        if trade['id'] == trade_id and trade['status'] == 'OPEN':
            trade['current_price'] = data.get('current', trade['current_price'])
            trade['pnl'] = data.get('pnl', trade['pnl'])
            trade['pips'] = data.get('pips', trade['pips'])
            
            # Broadcast
            socketio.emit('trade_update', trade, room='app')
            break
    
    return jsonify({'success': True})


@app.route('/webhook/ea/signal', methods=['POST'])
@require_ea_auth
def ea_signal():
    """EA reports new signal (not necessarily a trade)"""
    data = request.get_json()
    
    state['ea_status']['last_signal'] = {
        'type': data.get('type'),
        'symbol': data.get('symbol'),
        'strength': data.get('strength'),
        'timestamp': datetime.utcnow().isoformat(),
    }
    
    # Broadcast
    socketio.emit('new_signal', state['ea_status']['last_signal'], room='app')
    
    return jsonify({'success': True})


# ============================================
# 🔔 NOTIFICATION HELPERS
# ============================================

def create_alert(title: str, message: str, alert_type: str) -> Dict:
    """Create and store an alert"""
    alert = {
        'id': str(int(time.time() * 1000)),
        'title': title,
        'message': message,
        'type': alert_type,
        'timestamp': datetime.utcnow().isoformat(),
        'read': False,
    }
    
    state['alerts'].insert(0, alert)
    
    # Keep only last 100 alerts
    if len(state['alerts']) > 100:
        state['alerts'] = state['alerts'][:100]
    
    # Broadcast
    socketio.emit('new_alert', alert, room='app')
    
    return alert


def send_alert(title: str, message: str, alert_type: str = 'info'):
    """Send alert through all channels"""
    create_alert(title, message, alert_type)
    send_push_notification(title, message)
    send_telegram_alert(f"*{title}*\n{message}")
    send_discord_alert(title, message)


def send_push_notification(title: str, body: str):
    """Send push notification via Firebase"""
    if not Config.FIREBASE_SERVER_KEY:
        return
    
    for device_id, device_info in state['device_tokens'].items():
        try:
            response = requests.post(
                'https://fcm.googleapis.com/fcm/send',
                headers={
                    'Authorization': f'key={Config.FIREBASE_SERVER_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'to': device_info['token'],
                    'notification': {
                        'title': title,
                        'body': body,
                        'sound': 'default',
                    },
                    'data': {
                        'type': 'trade_alert',
                        'timestamp': datetime.utcnow().isoformat(),
                    },
                },
                timeout=5,
            )
            
            if response.status_code != 200:
                logger.error(f"FCM error: {response.text}")
                
        except Exception as e:
            logger.error(f"Push notification error: {e}")


def send_telegram_alert(message: str):
    """Send alert to Telegram"""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    
    try:
        requests.post(
            f'https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage',
            json={
                'chat_id': Config.TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'Markdown',
            },
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Telegram error: {e}")


def send_discord_alert(title: str, message: str):
    """Send alert to Discord"""
    if not Config.DISCORD_WEBHOOK_URL:
        return
    
    try:
        requests.post(
            Config.DISCORD_WEBHOOK_URL,
            json={
                'embeds': [{
                    'title': title,
                    'description': message,
                    'color': 0x6366F1,
                    'timestamp': datetime.utcnow().isoformat(),
                    'footer': {'text': '🥷 Trading Sensei'},
                }],
            },
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Discord error: {e}")


# ============================================
# 🔌 WEBSOCKET HANDLERS
# ============================================

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    logger.info(f"Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    state['connected_devices'].discard(request.sid)
    logger.info(f"Client disconnected: {request.sid}")


@socketio.on('join_app')
def handle_join_app(data):
    """App client joins the app room"""
    join_room('app')
    state['connected_devices'].add(request.sid)
    
    # Send current state
    emit('initial_state', {
        'account': state['account'],
        'ea_status': state['ea_status'],
        'trades': [t for t in state['trades'] if t.get('status') == 'OPEN'],
        'alerts': state['alerts'][:10],
    })
    
    logger.info(f"App joined: {request.sid}")


@socketio.on('join_ea')
def handle_join_ea(data):
    """EA joins the EA room"""
    ea_secret = data.get('secret', '')
    
    if not hmac.compare_digest(ea_secret, Config.EA_SECRET):
        emit('error', {'message': 'Invalid EA secret'})
        return
    
    join_room('ea')
    logger.info(f"EA connected: {request.sid}")
    
    # Notify apps
    socketio.emit('ea_connected', {'connected': True}, room='app')


@socketio.on('leave_ea')
def handle_leave_ea():
    """EA leaves"""
    leave_room('ea')
    state['ea_status']['connected'] = False
    
    # Notify apps
    socketio.emit('ea_disconnected', {'connected': False}, room='app')
    
    logger.info(f"EA disconnected: {request.sid}")


# ============================================
# 🚀 MAIN
# ============================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    print(f"""
    🥷 ====================================
       TRADING SENSEI WEBHOOK SERVER
    ====================================
    
    Server starting on port {port}
    
    📱 App API:     http://localhost:{port}/api/
    🤖 EA Webhook:  http://localhost:{port}/webhook/ea/
    🔌 WebSocket:   ws://localhost:{port}
    
    Environment Variables Required:
    - SECRET_KEY
    - JWT_SECRET
    - EA_SECRET
    - OANDA_API_TOKEN (optional)
    - FIREBASE_SERVER_KEY (optional)
    - TELEGRAM_BOT_TOKEN (optional)
    - DISCORD_WEBHOOK_URL (optional)
    
    🥷 Trade Like a Ninja!
    ====================================
    """)
    
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=os.environ.get('DEBUG', 'false').lower() == 'true',
    )
