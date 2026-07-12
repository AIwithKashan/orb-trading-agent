"""
Gold BOS Scanner Bot Manager
=============================
Manages the real-time Gold BOS scanning loop (separate from ORB scanner).
"""

import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
import pytz

from gold_strategy import GoldBOSTracker, fetch_gold_data

logger = logging.getLogger("GoldBot")

TIMEZONE_UTC = pytz.utc
TIMEZONE_PKT = pytz.timezone('Asia/Karachi')
SCAN_INTERVAL = 60  # Scan every 60 seconds

class GoldScannerBot:
    """Encapsulates the Gold BOS scanner state for a single user."""

    def __init__(self, uid: str, swing_lookback: int = 5, rr_ratio: float = 2.0, timeframe: str = "15m"):
        self.uid = uid
        self.running = True
        self.thread: Optional[threading.Thread] = None
        self.tracker = GoldBOSTracker(swing_lookback=swing_lookback, rr_ratio=rr_ratio)
        self.timeframe = timeframe
        self.activity = "Initializing Gold scanner..."
        self.logs: List[str] = []
        self._log_lock = threading.Lock()
        self.last_scan_time: Optional[str] = None

    def add_log(self, msg: str) -> None:
        with self._log_lock:
            timestamp = datetime.now(TIMEZONE_PKT).strftime("%H:%M:%S PKT")
            entry = f"{timestamp} [GOLD] {msg}"
            self.logs.append(entry)
            if len(self.logs) > 100:
                self.logs.pop(0)
            logger.info(f"[{self.uid[:8]}] {msg}")

    def update_params(self, swing_lookback: int = None, rr_ratio: float = None, timeframe: str = None):
        if swing_lookback:
            self.tracker.swing_lookback = swing_lookback
        if rr_ratio:
            self.tracker.rr_ratio = rr_ratio
        if timeframe:
            self.timeframe = timeframe

    def get_status(self) -> Dict[str, Any]:
        status = self.tracker.get_structure_levels()
        status["scanner_running"] = self.running
        status["timeframe"] = self.timeframe
        status["last_scan"] = self.last_scan_time
        status["activity"] = self.activity
        return status

def run_gold_scanner_loop(bot: GoldScannerBot) -> None:
    """Main scanning loop for Gold BOS strategy."""
    bot.add_log("Gold BOS scanner started.")
    bot.activity = "Gold scanner online."

    while bot.running:
        try:
            now = datetime.now(TIMEZONE_UTC)
            weekday = now.weekday()

            # Gold market is closed on weekends (Sat after 5PM EST to Sun 5PM EST)
            if weekday == 5 and now.hour >= 22:  # Saturday after ~5PM EST
                bot.activity = "Gold market closed (weekend). Standing by."
                if not _interruptible_sleep(bot, 300):
                    break
                continue
            if weekday == 6 and now.hour < 22:  # Sunday before ~5PM EST
                bot.activity = "Gold market closed (weekend). Standing by."
                if not _interruptible_sleep(bot, 300):
                    break
                continue

            # Fetch and analyze
            bot.activity = "Fetching Gold data..."
            df = fetch_gold_data(period="5d", interval=bot.timeframe)

            if df is not None and not df.empty:
                signal = bot.tracker.detect_bos(df)
                bot.last_scan_time = now.strftime("%H:%M:%S UTC")

                if signal:
                    bot.add_log(
                        f"BOS SIGNAL: {signal.direction} @ ${signal.entry_price:.2f} "
                        f"(SL: ${signal.stop_loss:.2f}, TP: ${signal.take_profit:.2f}, "
                        f"Confidence: {signal.confidence}%)"
                    )
                    bot.activity = f"BOS {signal.direction} detected @ ${signal.entry_price:.2f}"
                else:
                    bot.activity = (
                        f"Scanning Gold ({bot.timeframe}) — "
                        f"Bias: {bot.tracker.market_bias} — "
                        f"Price: ${bot.tracker.current_price:.2f}"
                    )
                    bot.add_log(f"Scan complete. Bias: {bot.tracker.market_bias}, Price: ${bot.tracker.current_price:.2f}")
            else:
                bot.add_log("No Gold data available. Retrying...")
                bot.activity = "Data fetch failed. Retrying..."

            if not _interruptible_sleep(bot, SCAN_INTERVAL):
                break

        except Exception as e:
            bot.add_log(f"Error: {e}")
            if not _interruptible_sleep(bot, 30):
                break

    bot.add_log("Gold BOS scanner stopped.")
    bot.activity = "Scanner stopped."

def _interruptible_sleep(bot: GoldScannerBot, seconds: float) -> bool:
    """Sleep in increments; returns False if bot is stopped."""
    elapsed = 0.0
    while elapsed < seconds:
        if not bot.running:
            return False
        time.sleep(min(0.5, seconds - elapsed))
        elapsed += 0.5
    return True

# ═══ Bot Registry ═══
_gold_registry: Dict[str, GoldScannerBot] = {}
_gold_lock = threading.Lock()

def start_gold_bot(uid: str, swing_lookback: int = 5, rr_ratio: float = 2.0, timeframe: str = "15m") -> GoldScannerBot:
    """Starts the Gold BOS scanner for a user."""
    with _gold_lock:
        if uid in _gold_registry:
            _gold_registry[uid].running = False

        bot = GoldScannerBot(uid, swing_lookback, rr_ratio, timeframe)
        bot.thread = threading.Thread(target=run_gold_scanner_loop, args=(bot,), daemon=True)
        bot.thread.start()
        _gold_registry[uid] = bot
        logger.info(f"Gold scanner started for user {uid[:8]}")
        return bot

def stop_gold_bot(uid: str) -> bool:
    """Stops the Gold scanner for a user."""
    with _gold_lock:
        bot = _gold_registry.get(uid)
        if bot:
            bot.running = False
            return True
        return False

def get_gold_bot(uid: str) -> Optional[GoldScannerBot]:
    """Returns the Gold bot instance for a user."""
    return _gold_registry.get(uid)
