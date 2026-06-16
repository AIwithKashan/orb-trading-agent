import time
import logging
import threading
from datetime import datetime, time as dt_time
from typing import Dict, Any, Optional, Set, List
import pytz

from broker import AlpacaBroker
from strategy import ORBTracker
import firebase_db
import config

logger = logging.getLogger("ORBBot")

# =====================================================================
# HIGH-VOLUME US STOCKS
# =====================================================================
US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX", "AMD", "INTC",
    "DIS", "KO", "PEP", "WMT", "COST", "TGT", "JPM", "BAC", "V", "MA",
    "XOM", "CVX", "JNJ", "PFE", "MRK", "UNH", "LLY", "HD", "NKE", "PG",
    "ORCL", "CSCO", "CRM", "ADBE", "PYPL", "SQ", "QCOM", "TXN", "MU", "AMAT",
    "SBUX", "MCD", "BA", "GE", "CAT", "DE", "QQQ", "SPY", "IWM", "DIA"
]

# Market Timing (US/Eastern)
TIMEZONE_EST = pytz.timezone('US/Eastern')
ORB_WINDOW_START = dt_time(9, 30)
ORB_WINDOW_END = dt_time(9, 35)
TRADING_WINDOW_END = dt_time(12, 30)
LIQUIDATION_TIME = dt_time(15, 55)
MARKET_CLOSE_TIME = dt_time(16, 0)

RISK_REWARD_RATIO = 2.0
LOOP_INTERVAL = 15


class UserBot:
    """Encapsulates the complete bot state for a single user."""
    
    def __init__(self, uid: str, broker: Optional[AlpacaBroker], settings: Dict[str, Any]):
        self.uid = uid
        self.broker = broker
        self.settings = settings
        self.dry_run = settings.get("dry_run", True)
        self.trade_limit = settings.get("trade_limit", 3)
        
        # State
        self.running = True
        self.thread: Optional[threading.Thread] = None
        self.activity = "Initializing..."
        self.current_prices: Dict[str, float] = {}
        self.active_trades: List[Dict[str, Any]] = []
        self.daily_trade_count = 0
        self.symbols_traded_today: Set[str] = set()
        self.orb_levels_calculated = False
        self.liquidated_today = False
        self.account_equity = 0.0
        self.logs: List[str] = []
        self._log_lock = threading.Lock()
        
        # Trackers
        self.trackers = {symbol: ORBTracker(symbol) for symbol in US_STOCKS}
        
        # Fetch equity
        if self.broker:
            self.account_equity = self.broker.get_account_equity()
    
    def add_log(self, msg: str):
        """Thread-safe log appender."""
        with self._log_lock:
            timestamp = datetime.now(TIMEZONE_EST).strftime("%H:%M:%S")
            entry = f"{timestamp} {msg}"
            self.logs.append(entry)
            if len(self.logs) > 50:
                self.logs.pop(0)
            logger.info(f"[{self.uid[:8]}] {msg}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the current bot status for API consumption."""
        # Update live P&L
        for trade in self.active_trades:
            symbol = trade["symbol"]
            price = self.current_prices.get(symbol, 0.0)
            if price > 0:
                entry = trade["entry_price"]
                qty = trade["qty"]
                if trade["side"] == "Long":
                    trade["unrealized_pl"] = round((price - entry) * qty, 2)
                else:
                    trade["unrealized_pl"] = round((entry - price) * qty, 2)
                trade["current_price"] = price
        
        return {
            "bot_running": self.running,
            "dry_run": self.dry_run,
            "trade_count": self.daily_trade_count,
            "max_trades": self.trade_limit,
            "prices": self.current_prices,
            "trades": self.active_trades,
            "logs": list(self.logs),
            "activity": self.activity,
            "account_equity": self.account_equity
        }

    def get_levels(self) -> Dict[str, Dict[str, float]]:
        """Returns ORB levels for all tracked symbols."""
        return {
            symbol: {
                "orb_high": t.orb_high,
                "orb_low": t.orb_low,
                "orb_mid": t.orb_mid
            }
            for symbol, t in self.trackers.items()
        }


def _interruptible_sleep(bot: UserBot, seconds: float) -> bool:
    """Sleeps in 1-second increments, returning False if bot is stopped."""
    elapsed = 0.0
    while elapsed < seconds:
        if not bot.running:
            return False
        time.sleep(min(1.0, seconds - elapsed))
        elapsed += 1.0
    return True


def run_bot_loop(bot: UserBot) -> None:
    """The main trading loop for a single user's bot instance."""
    bot.add_log("[SYSTEM] Trading engine started.")
    bot.activity = "System online. Starting trading loop."
    
    last_date = None
    
    while bot.running:
        try:
            now = datetime.now(TIMEZONE_EST)
            current_time = now.time()
            weekday = now.weekday()
            today = now.date()
            
            # Daily reset
            if last_date is None:
                last_date = today
            elif today != last_date:
                bot.add_log("[SYSTEM] New trading day. Resetting session.")
                bot.orb_levels_calculated = False
                bot.liquidated_today = False
                bot.daily_trade_count = 0
                bot.symbols_traded_today.clear()
                bot.active_trades.clear()
                last_date = today
                # Refresh equity
                if bot.broker:
                    bot.account_equity = bot.broker.get_account_equity()
            
            # Weekend
            if weekday >= 5:
                bot.activity = "Markets closed on weekends. Standing by."
                if not _interruptible_sleep(bot, 300):
                    break
                continue
            
            # After market close
            if current_time >= MARKET_CLOSE_TIME:
                if bot.orb_levels_calculated:
                    bot.add_log("[SYSTEM] Trading session ended. Resetting.")
                    bot.orb_levels_calculated = False
                    bot.liquidated_today = False
                    bot.daily_trade_count = 0
                    bot.symbols_traded_today.clear()
                bot.activity = "Market closed. Sleeping until tomorrow."
                if not _interruptible_sleep(bot, 300):
                    break
                continue
            
            # Wait for market open
            if current_time < ORB_WINDOW_START:
                start_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_START))
                secs = (start_dt - now).total_seconds()
                if secs > 0:
                    mins = int(secs / 60)
                    bot.activity = f"Market opens in {mins} min. Waiting..."
                    bot.add_log(f"[WAIT] Market opens in {mins} minutes.")
                    sleep_time = min(300, secs)
                    if not _interruptible_sleep(bot, sleep_time):
                        break
                    continue
            
            # Wait for ORB to form
            if current_time < ORB_WINDOW_END:
                end_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_END))
                secs = (end_dt - now).total_seconds()
                if secs > 0:
                    bot.activity = f"Opening range forming... {int(secs)}s remaining."
                    bot.add_log(f"[WAIT] ORB range forming. {int(secs)}s until 9:35 AM EST.")
                    bot.orb_levels_calculated = False
                    if not _interruptible_sleep(bot, secs):
                        break
                    continue
            
            # Calculate ORB levels
            if not bot.orb_levels_calculated:
                bot.activity = "Calculating ORB levels for 50 stocks..."
                bot.add_log("[CALC] Computing opening range breakout levels...")
                
                start_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_START))
                end_dt = TIMEZONE_EST.localize(datetime.combine(today, ORB_WINDOW_END))
                
                for symbol in US_STOCKS:
                    tracker = bot.trackers[symbol]
                    try:
                        if bot.broker and not bot.dry_run:
                            bars = bot.broker.api.get_bars(
                                symbol=symbol, timeframe="5Min",
                                start=start_dt.isoformat(), end=end_dt.isoformat()
                            )
                            tracker.calculate_orb_levels(bars)
                        else:
                            # Dry-run mock levels
                            import random
                            base = {"AAPL": 180, "MSFT": 420, "GOOGL": 170, "AMZN": 185, 
                                    "NVDA": 850, "TSLA": 175, "META": 500, "NFLX": 600,
                                    "AMD": 160, "INTC": 30}.get(symbol, 150 + random.uniform(-50, 50))
                            tracker.orb_high = base * 1.005
                            tracker.orb_low = base * 0.995
                            tracker.orb_mid = base
                            tracker.was_inside_range = False
                    except Exception as e:
                        bot.add_log(f"[ERROR] ORB calc failed for {symbol}: {e}")
                
                bot.orb_levels_calculated = True
                bot.add_log("[CALC] ORB levels calculated for all stocks.")
            
            # EOD Liquidation
            if current_time >= LIQUIDATION_TIME and not bot.liquidated_today:
                bot.activity = "EOD Liquidation active."
                bot.add_log("[LIQUIDATE] End-of-day liquidation triggered.")
                if bot.broker and not bot.dry_run:
                    bot.broker.cancel_all_orders_and_close_positions()
                bot.liquidated_today = True
                if not _interruptible_sleep(bot, 30):
                    break
                continue
            
            # Active trading window
            if ORB_WINDOW_END <= current_time < TRADING_WINDOW_END:
                if bot.daily_trade_count >= bot.trade_limit:
                    bot.activity = f"Trade limit ({bot.trade_limit}) reached. Monitoring."
                    if not _interruptible_sleep(bot, LOOP_INTERVAL):
                        break
                    continue
                
                scan_time = now.strftime('%H:%M:%S')
                bot.activity = f"Scanning 50 stocks... (Last: {scan_time} EST)"
                
                # Fetch prices
                if bot.broker and not bot.dry_run:
                    prices = bot.broker.get_latest_prices(US_STOCKS)
                else:
                    import random
                    prices = {}
                    for s in US_STOCKS:
                        t = bot.trackers[s]
                        if t.orb_high == 0:
                            continue
                        choice = random.choice(["mid", "high", "low", "mid", "mid"])
                        if choice == "high":
                            prices[s] = t.orb_high + (t.orb_high * 0.015)
                        elif choice == "low":
                            prices[s] = t.orb_low - (t.orb_low * 0.015)
                        else:
                            prices[s] = t.orb_mid + random.uniform(-0.5, 0.5)
                
                for s, p in prices.items():
                    bot.current_prices[s] = p
                
                # Trigger-zone breakout evaluation
                for symbol in US_STOCKS:
                    if symbol in bot.symbols_traded_today:
                        continue
                    if bot.daily_trade_count >= bot.trade_limit:
                        break
                    
                    tracker = bot.trackers[symbol]
                    price = bot.current_prices.get(symbol, 0.0)
                    if price == 0 or tracker.orb_high == 0:
                        continue
                    
                    # Track inside-range state
                    is_inside = tracker.orb_low <= price <= tracker.orb_high
                    if is_inside:
                        tracker.was_inside_range = True
                    
                    triggered = False
                    side = None
                    sl = tp = 0.0
                    
                    sl_pct = bot.settings.get("stop_loss_pct", 0.0)
                    tp_pct = bot.settings.get("take_profit_pct", 0.0)
                    
                    if tracker.was_inside_range:
                        if price >= tracker.orb_high:
                            side = "Long"
                            if sl_pct > 0:
                                sl = price * (1 - sl_pct / 100.0)
                            else:
                                sl = tracker.orb_mid
                            
                            if tp_pct > 0:
                                tp = price * (1 + tp_pct / 100.0)
                            else:
                                risk = price - sl
                                tp = price + (risk * RISK_REWARD_RATIO)
                            triggered = True
                            tracker.was_inside_range = False
                        elif price <= tracker.orb_low:
                            side = "Short"
                            if sl_pct > 0:
                                sl = price * (1 + sl_pct / 100.0)
                            else:
                                sl = tracker.orb_mid
                            
                            if tp_pct > 0:
                                tp = price * (1 - tp_pct / 100.0)
                            else:
                                risk = sl - price
                                tp = price - (risk * RISK_REWARD_RATIO)
                            triggered = True
                            tracker.was_inside_range = False
                    elif not is_inside:
                        continue
                    
                    if triggered and side:
                        try:
                            equity = bot.account_equity if bot.account_equity > 0 else 500.0
                            qty = tracker.calculate_position_size(price, sl, equity=equity)
                            qty = round(qty, 4)
                            
                            bot.add_log(
                                f"[TRADE] {side} {symbol} @ ${price:.2f} | "
                                f"SL: ${sl:.2f} | TP: ${tp:.2f} | Qty: {qty}"
                            )
                            
                            order_placed = False
                            if not bot.dry_run and bot.broker:
                                order = bot.broker.submit_bracket_order(
                                    symbol=symbol, qty=qty,
                                    side="buy" if side == "Long" else "sell",
                                    take_profit_price=tp, stop_loss_price=sl
                                )
                                order_placed = order is not None
                            else:
                                order_placed = True
                                bot.add_log(f"[DRY-RUN] Simulated {side} for {symbol}.")
                            
                            if order_placed:
                                trade_record = {
                                    "symbol": symbol,
                                    "side": side,
                                    "qty": qty,
                                    "entry_price": price,
                                    "stop_loss": sl,
                                    "take_profit": tp,
                                    "orb_high": tracker.orb_high,
                                    "orb_low": tracker.orb_low,
                                    "order_type": "Bracket (Market)",
                                    "status": "FILLED",
                                    "timestamp": datetime.now(pytz.utc).isoformat(),
                                    "pnl": None  # Filled when closed
                                }
                                bot.active_trades.append(trade_record)
                                
                                # Log to Firestore
                                firebase_db.log_trade(bot.uid, trade_record)
                                
                                bot.symbols_traded_today.add(symbol)
                                bot.daily_trade_count += 1
                                bot.add_log(f"[TRADE] Trades today: {bot.daily_trade_count}/{bot.trade_limit}")
                        except Exception as e:
                            bot.add_log(f"[ERROR] Trade execution failed for {symbol}: {e}")
            
            if not _interruptible_sleep(bot, LOOP_INTERVAL):
                break
                
        except Exception as e:
            bot.add_log(f"[ERROR] Bot loop error: {e}")
            if not _interruptible_sleep(bot, 10):
                break
    
    bot.add_log("[SYSTEM] Trading engine stopped.")
    bot.activity = "Bot stopped."


# =====================================================================
# BOT REGISTRY — Manages all active user bot threads
# =====================================================================
_bot_registry: Dict[str, UserBot] = {}
_registry_lock = threading.Lock()


def start_bot(uid: str, alpaca_keys: Optional[Dict[str, str]], settings: Dict[str, Any]) -> UserBot:
    """Starts a trading bot for the given user. Returns the UserBot instance."""
    with _registry_lock:
        # Stop existing bot if running
        if uid in _bot_registry:
            _bot_registry[uid].running = False
        
        # Create broker
        broker = None
        if alpaca_keys and alpaca_keys.get("api_key") and alpaca_keys.get("secret_key"):
            try:
                broker = AlpacaBroker(
                    api_key=alpaca_keys["api_key"],
                    secret_key=alpaca_keys["secret_key"],
                    base_url=config.ALPACA_BASE_URL
                )
            except Exception as e:
                logger.error(f"Failed to create broker for {uid}: {e}")
        
        bot = UserBot(uid, broker, settings)
        bot.thread = threading.Thread(target=run_bot_loop, args=(bot,), daemon=True)
        bot.thread.start()
        
        _bot_registry[uid] = bot
        logger.info(f"Bot started for user {uid[:8]}")
        return bot


def stop_bot(uid: str) -> bool:
    """Stops the trading bot for the given user."""
    with _registry_lock:
        bot = _bot_registry.get(uid)
        if bot:
            bot.running = False
            logger.info(f"Bot stopped for user {uid[:8]}")
            return True
        return False


def get_bot(uid: str) -> Optional[UserBot]:
    """Returns the UserBot instance for the given user, or None."""
    return _bot_registry.get(uid)


def get_all_bots() -> Dict[str, UserBot]:
    """Returns all active bot instances."""
    return dict(_bot_registry)
