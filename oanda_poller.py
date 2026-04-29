"""Background poller that mirrors OANDA account state into AppState.

Runs only when the EA isn't pushing fresh heartbeats — the EA is the
authoritative source when it's online. Each successful poll emits an
`account_update` event so connected dashboards refresh without waiting
for their next REST poll.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import requests

from app_state import AppState
from oanda_client import OandaClient

logger = logging.getLogger(__name__)

EA_GRACE_SECONDS = 30


def start(
    oanda: OandaClient,
    state: AppState,
    on_account: Callable[[dict[str, Any]], None],
    interval: int = 30,
) -> threading.Thread | None:
    """Start the poller in a daemon thread. No-op if OANDA isn't configured.

    `on_account` is invoked with the freshly written account snapshot after
    each successful poll. Callers typically broadcast it over Socket.IO and
    feed it through the risk circuit breaker.
    """
    if not oanda.configured:
        logger.info("OANDA poller: not started (client not configured)")
        return None

    def loop() -> None:
        logger.info("OANDA poller: started (interval=%ss)", interval)
        try:
            while True:
                try:
                    if not _ea_recently_heartbeated(state):
                        account = oanda.account_summary()
                        state.update_account(**{
                            k: account[k] for k in (
                                "balance", "equity", "margin_used", "margin_available",
                                "unrealized_pnl", "total_pnl", "open_trades",
                            )
                        })
                        on_account(state.get_account())
                except requests.RequestException as e:
                    logger.warning("OANDA poller: request failed: %s", e)
                except Exception:
                    logger.exception("OANDA poller: unexpected error")
                time.sleep(interval)
        except SystemExit:
            return

    thread = threading.Thread(target=loop, daemon=True, name="oanda-poller")
    thread.start()
    return thread


def _ea_recently_heartbeated(state: AppState) -> bool:
    last_hb = state.get_ea_status().get("last_heartbeat")
    if not last_hb:
        return False
    try:
        ts = datetime.fromisoformat(last_hb)
    except ValueError:
        return False
    return datetime.utcnow() - ts < timedelta(seconds=EA_GRACE_SECONDS)
