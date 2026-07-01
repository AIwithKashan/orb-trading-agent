import time
import logging
import threading
from datetime import datetime, time as dt_time, timedelta
from typing import Dict, Any, Optional, List
import pytz

from strategy import ORBTracker
import firebase_db
import config

logger = logging.getLogger("ORBBot")

# =====================================================================
# DEFAULT 50 HIGH-VOLUME US STOCKS
# =====================================================================
DEFAULT_US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX", "AMD", "INTC",
    "DIS", "KO", "PEP", "WMT", "COST", "TGT", "JPM", "BAC", "V", "MA",
    "XOM", "CVX", "JNJ", "PFE", "MRK", "UNH", "LLY", "HD", "NKE", "PG",
    "ORCL", "CSCO", "CRM", "ADBE", "PYPL", "QCOM", "TXN", "MU", "AMAT", "UBER",
    "SBUX", "MCD", "BA", "GE", "CAT", "DE", "QQQ", "SPY", "IWM", "DIA"
]

# Market Timing (US/Eastern)
TIMEZONE_EST = pytz.timezone('US/Eastern')
TIMEZONE_PKT = pytz.timezone('Asia/Karachi')
ORB_WINDOW_START = dt_time(9, 30)
ORB_WINDOW_END   = dt_time(9, 45)
MARKET_CLOSE_TIME = dt_time(16, 0)
LOOP_INTERVAL = 3   # seconds between each price scan cycle


class ScannerBot:
    """Encapsulates the real-time ORB screener state for a single user."""

    def __init__(self, uid: str, symbols: List[str]):
        self.uid = uid
        self.running = True
        self.thread: Optional[threading.Thread] = None
        self.activity = "Initializing screener..."
        self.current_prices: Dict[str, float] = {}
        self.orb_levels_calculated = False
        self.logs: List[str] = []
        self._log_lock = threading.Lock()
        self._symbols_lock = threading.Lock()

        # Breakout signals fired today (list of dicts)
        self.breakout_signals: List[Dict[str, Any]] = []
        # Set of symbols that already fired a signal today (reset each new day)
        self.signals_fired_today: set = set()

        # Build trackers from starting symbol list
        self._symbols: List[str] = list(symbols)
        self.trackers: Dict[str, ORBTracker] = {s: ORBTracker(s) for s in self._symbols}

    # ------------------------------------------------------------------
    # Symbol management
    # ------------------------------------------------------------------
    def get_symbols(self) -> List[str]:
        with self._symbols_lock:
            return list(self._symbols)

    def update_symbols(self, new_symbols: List[str]) -> None:
        """Hot-swap the tracked symbol list; new symbols get fresh trackers."""
        with self._symbols_lock:
            new_set = set(new_symbols)
            old_set = set(self._symbols)

            # Remove stale trackers
            for s in old_set - new_set:
                self.trackers.pop(s, None)
                self.current_prices.pop(s, None)

            # Add new trackers (they will be calculated on next ORB window)
            for s in new_set - old_set:
                self.trackers[s] = ORBTracker(s)

            self._symbols = list(new_set)
            # If ORB was already calculated, recalculate for new symbols next cycle
            if new_set - old_set:
                self.orb_levels_calculated = False

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def add_log(self, msg: str) -> None:
        with self._log_lock:
            # Show timestamps in PKT for user-facing logs
            timestamp = datetime.now(TIMEZONE_PKT).strftime("%H:%M:%S PKT")
            entry = f"{timestamp} {msg}"
            self.logs.append(entry)
            if len(self.logs) > 100:
                self.logs.pop(0)
            logger.info(f"[{self.uid[:8]}] {msg}")

    # ------------------------------------------------------------------
    # Status for API
    # ------------------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "screener_running": self.running,
            "symbols_count": len(self._symbols),
            "prices": dict(self.current_prices),
            "logs": list(self.logs),
            "activity": self.activity,
            "orb_levels_calculated": self.orb_levels_calculated,
            "breakout_signals": list(self.breakout_signals),
            "signals_today": len(self.breakout_signals),
        }

    def get_levels(self) -> Dict[str, Dict[str, float]]:
        return {
            symbol: {
                "orb_high": t.orb_high,
                "orb_low":  t.orb_low,
                "orb_mid":  t.orb_mid,
            }
            for symbol, t in self.trackers.items()
        }


# =====================================================================
# HELPERS
# =====================================================================

def _interruptible_sleep(bot: ScannerBot, seconds: float) -> bool:
    """Sleeps in 0.5-second increments; returns False if bot is stopped."""
    elapsed = 0.0
    while elapsed < seconds:
        if not bot.running:
            return False
        time.sleep(min(0.5, seconds - elapsed))
        elapsed += 0.5
    return True


def _calculate_orb_levels(bot: ScannerBot, today) -> None:
    """Download 5-min OHLC bars for all tracked symbols and calculate ORB levels."""
    symbols = bot.get_symbols()
    bot.activity = f"Calculating ORB levels for {len(symbols)} stocks..."
    bot.add_log(f"[CALC] Computing opening range breakout levels for {len(symbols)} symbols...")

    today_str    = today.strftime("%Y-%m-%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    yfinance_df = None
    try:
        import yfinance as yf
        bot.add_log("[CALC] Fetching OHLC data via Yahoo Finance...")
        yfinance_df = yf.download(
            symbols,
            start=today_str,
            end=tomorrow_str,
            interval="5m",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )
    except Exception as e:
        bot.add_log(f"[WARNING] Yahoo Finance fetch failed: {e}")

    for symbol in symbols:
        tracker = bot.trackers.get(symbol)
        if tracker is None:
            continue

        calculated = False
        if yfinance_df is not None and not yfinance_df.empty:
            try:
                if symbol in yfinance_df.columns.levels[0]:
                    df = yfinance_df[symbol].dropna()
                    if not df.empty:
                        tracker.calculate_orb_levels(df)
                        calculated = True
            except Exception:
                pass

        if not calculated:
            # If Yahoo fails for this symbol and server-level Alpaca keys are set, fall back
            try:
                if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
                    from broker import AlpacaBroker
                    _broker = AlpacaBroker(
                        api_key=config.ALPACA_API_KEY,
                        secret_key=config.ALPACA_SECRET_KEY,
                        base_url=config.ALPACA_BASE_URL,
                    )
                    start_dt = TIMEZONE_EST.localize(
                        datetime.combine(today, ORB_WINDOW_START)
                    )
                    end_dt = TIMEZONE_EST.localize(
                        datetime.combine(today, ORB_WINDOW_END)
                    )
                    try:
                        bars = _broker.api.get_bars(
                            symbol=symbol, timeframe="5Min",
                            start=start_dt.isoformat(), end=end_dt.isoformat(),
                            feed="sip"
                        )
                    except Exception:
                        bars = _broker.api.get_bars(
                            symbol=symbol, timeframe="5Min",
                            start=start_dt.isoformat(), end=end_dt.isoformat(),
                            feed="iex"
                        )
                    tracker.calculate_orb_levels(bars)
            except Exception as e:
                bot.add_log(f"[WARNING] ORB calc fallback failed for {symbol}: {e}")

    bot.orb_levels_calculated = True
    bot.add_log(f"[CALC] ORB levels ready for {len(symbols)} symbols.")


def _fetch_prices(bot: ScannerBot) -> Dict[str, float]:
    """Batch-fetch latest prices. Uses Alpaca if configured, else yfinance snapshot."""
    symbols = bot.get_symbols()
    prices: Dict[str, float] = {}

    if config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY:
        try:
            from broker import AlpacaBroker
            _broker = AlpacaBroker(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
                base_url=config.ALPACA_BASE_URL,
            )
            prices = _broker.get_latest_prices(symbols)
            if prices:
                return prices
        except Exception as e:
            bot.add_log(f"[WARNING] Alpaca price fetch failed, falling back to yfinance: {e}")

    # Fallback: yfinance fast quotes
    try:
        import yfinance as yf
        tickers_obj = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                info = tickers_obj.tickers[sym].fast_info
                price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                if price:
                    prices[sym] = float(price)
            except Exception:
                pass
    except Exception as e:
        bot.add_log(f"[WARNING] yfinance price fallback also failed: {e}")

    return prices


# =====================================================================
# MAIN SCANNER LOOP
# =====================================================================

def run_scanner_loop(bot: ScannerBot) -> None:
    """The main real-time scanning loop for a single user's screener instance."""
    bot.add_log("[SYSTEM] Screener engine started.")
    bot.activity = "Screener online. Starting scan loop."

    last_date = None

    while bot.running:
        try:
            now = datetime.now(TIMEZONE_EST)
            current_time = now.time()
            weekday = now.weekday()
            today = now.date()

            # ---------- Daily reset ----------
            if last_date is None:
                last_date = today
            elif today != last_date:
                bot.add_log("[SYSTEM] New trading day. Resetting session.")
                bot.orb_levels_calculated = False
                bot.breakout_signals.clear()
                bot.signals_fired_today.clear()
                last_date = today

            # ---------- Weekend ----------
            if weekday >= 5:
                bot.activity = "Markets closed (weekend). Screener standing by."
                if not _interruptible_sleep(bot, 300):
                    break
                continue

            # ---------- After market close ----------
            if current_time >= MARKET_CLOSE_TIME:
                if bot.orb_levels_calculated:
                    bot.orb_levels_calculated = False
                bot.activity = "Market closed. Screener sleeping until tomorrow."
                if not _interruptible_sleep(bot, 300):
                    break
                continue

            # ---------- Before market open ----------
            if current_time < ORB_WINDOW_START:
                start_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_START))
                secs = (start_dt - now).total_seconds()
                if secs > 0:
                    mins = int(secs / 60)
                    bot.activity = f"Market opens in {mins} min. Screener on standby."
                    sleep_time = min(300, secs)
                    if not _interruptible_sleep(bot, sleep_time):
                        break
                    continue

            # ---------- ORB window forming ----------
            if current_time < ORB_WINDOW_END:
                end_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_END))
                secs = (end_dt - now).total_seconds()
                if secs > 0:
                    bot.activity = f"Opening range forming... {int(secs)}s until 9:45 AM EST."
                    bot.orb_levels_calculated = False
                    if not _interruptible_sleep(bot, secs):
                        break
                    continue

            # ---------- Calculate ORB levels once per day ----------
            if not bot.orb_levels_calculated:
                _calculate_orb_levels(bot, today)

            # ---------- Active scanning window ----------
            prices = _fetch_prices(bot)
            for sym, price in prices.items():
                bot.current_prices[sym] = price

            scan_time = now.astimezone(TIMEZONE_PKT).strftime("%H:%M:%S")
            bot.activity = (
                f"Scanning {len(bot.get_symbols())} stocks — "
                f"{len(bot.breakout_signals)} signals today (Last: {scan_time} PKT)"
            )

            # ---------- Detect breakout signals ----------
            symbols = bot.get_symbols()
            for symbol in symbols:
                tracker = bot.trackers.get(symbol)
                price = bot.current_prices.get(symbol, 0.0)

                if not tracker or tracker.orb_high == 0 or price == 0:
                    continue

                if symbol in bot.signals_fired_today:
                    continue  # Already signalled today; don't repeat

                direction = None
                if price >= tracker.orb_high:
                    direction = "BULLISH"
                elif price <= tracker.orb_low:
                    direction = "BEARISH"

                if direction:
                    now_pkt = now.astimezone(TIMEZONE_PKT)
                    signal = {
                        "symbol": symbol,
                        "direction": direction,
                        "price": round(price, 2),
                        "orb_high": round(tracker.orb_high, 2),
                        "orb_low": round(tracker.orb_low, 2),
                        "orb_mid": round(tracker.orb_mid, 2),
                        "time": now_pkt.strftime("%H:%M:%S PKT"),
                        "timestamp": now.isoformat(),
                    }
                    bot.breakout_signals.insert(0, signal)
                    bot.signals_fired_today.add(symbol)
                    if len(bot.breakout_signals) > 100:
                        bot.breakout_signals.pop()
                    bot.add_log(
                        f"[SIGNAL] {direction} breakout: {symbol} @ ${price:.2f} "
                        f"(ORB High: ${tracker.orb_high:.2f} / Low: ${tracker.orb_low:.2f})"
                    )

            if not _interruptible_sleep(bot, LOOP_INTERVAL):
                break

        except Exception as e:
            bot.add_log(f"[ERROR] Scanner loop error: {e}")
            if not _interruptible_sleep(bot, 10):
                break

    bot.add_log("[SYSTEM] Screener engine stopped.")
    bot.activity = "Screener stopped."


# =====================================================================
# BOT REGISTRY
# =====================================================================
_bot_registry: Dict[str, ScannerBot] = {}
_registry_lock = threading.Lock()


def start_bot(uid: str, symbols: Optional[List[str]] = None) -> ScannerBot:
    """Starts (or restarts) the screener for a given user. Returns the ScannerBot instance."""
    with _registry_lock:
        # Stop existing bot if any
        if uid in _bot_registry:
            _bot_registry[uid].running = False

        # Resolve symbol list: user custom list > default 50
        if not symbols:
            symbols = firebase_db.get_tracked_tickers(uid) or DEFAULT_US_STOCKS

        bot = ScannerBot(uid, symbols)
        bot.thread = threading.Thread(target=run_scanner_loop, args=(bot,), daemon=True)
        bot.thread.start()

        _bot_registry[uid] = bot
        logger.info(f"Screener started for user {uid[:8]} ({len(symbols)} symbols)")
        return bot


def stop_bot(uid: str) -> bool:
    """Stops the screener for the given user."""
    with _registry_lock:
        bot = _bot_registry.get(uid)
        if bot:
            bot.running = False
            logger.info(f"Screener stopped for user {uid[:8]}")
            return True
        return False


def get_bot(uid: str) -> Optional[ScannerBot]:
    """Returns the ScannerBot instance for the given user, or None."""
    return _bot_registry.get(uid)


def get_all_bots() -> Dict[str, ScannerBot]:
    """Returns all active screener instances."""
    return dict(_bot_registry)
