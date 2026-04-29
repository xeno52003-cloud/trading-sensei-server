"""Background watchdog that alerts on EA alive ⇄ silent transitions.

State is held in the thread, not the StateStore, so a server restart
doesn't fire a phantom "back online" alert just because the in-memory
flag was cleared. First poll never alerts — there's no prior state to
compare against.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from app_state import AppState

logger = logging.getLogger(__name__)


def start(
    state: AppState,
    on_alert: Callable[[str, str, str], None],
    disconnect_after_sec: int = 60,
    check_interval: int = 15,
) -> threading.Thread:
    """Daemon thread; never blocks anything."""

    def loop() -> None:
        logger.info("EA watchdog: started (disconnect after %ss)", disconnect_after_sec)
        was_alive: bool | None = None
        try:
            while True:
                alive = _is_alive(state, disconnect_after_sec)

                if was_alive is True and not alive:
                    on_alert(
                        "🔴 EA disconnected",
                        f"No heartbeat for {disconnect_after_sec}s — trading paused until it reconnects.",
                        "emergency",
                    )
                elif was_alive is False and alive:
                    on_alert(
                        "🟢 EA back online",
                        "Heartbeats resumed.",
                        "info",
                    )

                was_alive = alive
                time.sleep(check_interval)
        except SystemExit:
            return

    thread = threading.Thread(target=loop, daemon=True, name="ea-watchdog")
    thread.start()
    return thread


def _is_alive(state: AppState, threshold_sec: int) -> bool:
    last_hb = state.get_ea_status().get("last_heartbeat")
    if not last_hb:
        return False
    try:
        ts = datetime.fromisoformat(last_hb)
    except ValueError:
        return False
    return datetime.utcnow() - ts < timedelta(seconds=threshold_sec)
