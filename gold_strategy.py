"""
Gold BOS (Break of Structure) Strategy Module
===============================================
Implements Smart Money Concepts Break of Structure detection for Gold (XAU/USD).
"""

import logging
import pytz
from typing import Any, Optional, List, Dict, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

logger = logging.getLogger("gold_strategy")

@dataclass
class SwingPoint:
    """Represents a detected swing high or swing low."""
    timestamp: datetime
    price: float
    type: str  # "high" or "low"
    index: int = 0

@dataclass
class BOSSignal:
    """Represents a Break of Structure signal."""
    timestamp: str
    direction: str  # "BULLISH" or "BEARISH"
    entry_price: float
    stop_loss: float
    take_profit: float
    broken_level: float
    swing_type: str  # "swing_high" or "swing_low"
    confidence: float = 0.0

class GoldBOSTracker:
    """
    Break of Structure (BOS) Strategy Tracker for Gold.
    Monitors Gold price action, detects swing points, and identifies BOS events.
    """

    TIMEZONE_UTC = pytz.utc

    def __init__(self, symbol: str = 'GC=F', swing_lookback: int = 5, rr_ratio: float = 2.0) -> None:
        self.symbol = symbol
        self.swing_lookback = swing_lookback
        self.rr_ratio = rr_ratio

        self.swing_highs: List[SwingPoint] = []
        self.swing_lows: List[SwingPoint] = []
        self.last_bos_signal: Optional[BOSSignal] = None
        self.market_bias: str = "NEUTRAL"
        self.current_price: float = 0.0
        self.last_update: Optional[datetime] = None

        self._candles: Optional[pd.DataFrame] = None
        self._signals_history: List[BOSSignal] = []
        self._bos_detected: bool = False

    def detect_swing_points(self, df: pd.DataFrame) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Detects swing highs and swing lows from OHLC data.
        A swing high: candle high > highs of N candles on both sides.
        A swing low: candle low < lows of N candles on both sides.
        """
        highs = df['High'].values
        lows = df['Low'].values
        n = len(df)
        lookback = self.swing_lookback

        swing_highs = []
        swing_lows = []

        for i in range(lookback, n - lookback):
            # Check swing high
            is_swing_high = True
            for j in range(1, lookback + 1):
                if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                    is_swing_high = False
                    break

            if is_swing_high:
                ts = df.index[i] if hasattr(df.index[i], 'tzinfo') else datetime.now(self.TIMEZONE_UTC)
                swing_highs.append(SwingPoint(
                    timestamp=ts, price=float(highs[i]), type="high", index=i
                ))

            # Check swing low
            is_swing_low = True
            for j in range(1, lookback + 1):
                if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                    is_swing_low = False
                    break

            if is_swing_low:
                ts = df.index[i] if hasattr(df.index[i], 'tzinfo') else datetime.now(self.TIMEZONE_UTC)
                swing_lows.append(SwingPoint(
                    timestamp=ts, price=float(lows[i]), type="low", index=i
                ))

        return swing_highs, swing_lows

    def detect_bos(self, df: pd.DataFrame) -> Optional[BOSSignal]:
        """
        Detects Break of Structure from the latest candle data.
        Bullish BOS: Current candle closes above the most recent swing high.
        Bearish BOS: Current candle closes below the most recent swing low.
        """
        if df.empty or len(df) < (self.swing_lookback * 2 + 5):
            return None

        # Detect swing points (excluding last 2 candles for fresh detection)
        analysis_df = df.iloc[:-2]
        swing_highs, swing_lows = self.detect_swing_points(analysis_df)

        self.swing_highs = swing_highs
        self.swing_lows = swing_lows

        if not swing_highs and not swing_lows:
            return None

        # Get the latest candle
        latest_candle = df.iloc[-1]
        current_close = float(latest_candle['Close'])
        self.current_price = current_close

        # Check for Bullish BOS
        if swing_highs:
            last_swing_high = swing_highs[-1]
            if current_close > last_swing_high.price:
                stop_loss = last_swing_high.price
                if swing_lows:
                    nearest_low = swing_lows[-1]
                    if nearest_low.price < last_swing_high.price:
                        stop_loss = nearest_low.price

                risk = current_close - stop_loss
                take_profit = current_close + (risk * self.rr_ratio)
                confidence = min(100, (current_close - last_swing_high.price) / last_swing_high.price * 10000)

                signal = BOSSignal(
                    timestamp=datetime.now(self.TIMEZONE_UTC).strftime("%Y-%m-%d %H:%M UTC"),
                    direction="BULLISH",
                    entry_price=round(current_close, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    broken_level=round(last_swing_high.price, 2),
                    swing_type="swing_high",
                    confidence=round(confidence, 1)
                )

                self.market_bias = "BULLISH"
                self.last_bos_signal = signal
                self._bos_detected = True
                self._signals_history.insert(0, signal)
                if len(self._signals_history) > 50:
                    self._signals_history.pop()
                return signal

        # Check for Bearish BOS
        if swing_lows:
            last_swing_low = swing_lows[-1]
            if current_close < last_swing_low.price:
                stop_loss = last_swing_low.price
                if swing_highs:
                    nearest_high = swing_highs[-1]
                    if nearest_high.price > last_swing_low.price:
                        stop_loss = nearest_high.price

                risk = stop_loss - current_close
                take_profit = current_close - (risk * self.rr_ratio)
                confidence = min(100, (last_swing_low.price - current_close) / last_swing_low.price * 10000)

                signal = BOSSignal(
                    timestamp=datetime.now(self.TIMEZONE_UTC).strftime("%Y-%m-%d %H:%M UTC"),
                    direction="BEARISH",
                    entry_price=round(current_close, 2),
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    broken_level=round(last_swing_low.price, 2),
                    swing_type="swing_low",
                    confidence=round(confidence, 1)
                )

                self.market_bias = "BEARISH"
                self.last_bos_signal = signal
                self._bos_detected = True
                self._signals_history.insert(0, signal)
                if len(self._signals_history) > 50:
                    self._signals_history.pop()
                return signal

        # Determine bias from structure
        if swing_highs and swing_lows:
            if swing_highs[-1].index > swing_lows[-1].index:
                self.market_bias = "BULLISH"
            else:
                self.market_bias = "BEARISH"

        return None

    def get_structure_levels(self) -> Dict[str, Any]:
        """Returns current market structure levels for UI."""
        result = {
            "market_bias": self.market_bias,
            "current_price": self.current_price,
            "bos_detected": self._bos_detected,
            "swing_highs": [{"price": sh.price, "time": str(sh.timestamp)} for sh in self.swing_highs[-5:]],
            "swing_lows": [{"price": sl.price, "time": str(sl.timestamp)} for sl in self.swing_lows[-5:]],
            "signals_history": [
                {
                    "direction": s.direction,
                    "entry": s.entry_price,
                    "sl": s.stop_loss,
                    "tp": s.take_profit,
                    "broken_level": s.broken_level,
                    "confidence": s.confidence,
                    "time": s.timestamp,
                } for s in self._signals_history[:20]
            ],
        }

        if self.last_bos_signal:
            result["last_signal"] = {
                "direction": self.last_bos_signal.direction,
                "entry": self.last_bos_signal.entry_price,
                "sl": self.last_bos_signal.stop_loss,
                "tp": self.last_bos_signal.take_profit,
                "broken_level": self.last_bos_signal.broken_level,
                "confidence": self.last_bos_signal.confidence,
                "time": self.last_bos_signal.timestamp,
            }

        return result


def fetch_gold_data(period: str = "5d", interval: str = "15m") -> Optional[pd.DataFrame]:
    """
    Fetches Gold price data from Yahoo Finance.
    """
    try:
        import yfinance as yf

        df = yf.download("GC=F", period=period, interval=interval, progress=False)

        if df.empty:
            logger.warning("No Gold data returned from Yahoo Finance.")
            return None

        # Flatten multi-index columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        # Ensure timezone aware
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')

        return df

    except Exception as e:
        logger.error(f"Error fetching Gold data: {e}")
        return None

def run_gold_bos_backtest(start_date_str: str, end_date_str: str,
                          risk_dollars: float = 10.0, rr_ratio: float = 2.0,
                          swing_lookback: int = 5, interval: str = "15m") -> Dict[str, Any]:
    """
    Backtests the Gold BOS strategy over a date range.
    """
    import yfinance as yf

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except Exception as e:
        return {"status": "error", "message": f"Invalid date format: {e}"}

    # yfinance limits for intraday data
    limit_date = datetime.now() - timedelta(days=60)
    if start_date < limit_date:
        start_date = limit_date
        start_date_str = start_date.strftime("%Y-%m-%d")

    end_date_excl = (end_date + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        df = yf.download("GC=F", start=start_date_str, end=end_date_excl,
                         interval=interval, progress=False)
        if df.empty:
            return {"status": "error", "message": "No Gold data available for this period."}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
    except Exception as e:
        return {"status": "error", "message": f"Data fetch failed: {e}"}

    tracker = GoldBOSTracker(swing_lookback=swing_lookback, rr_ratio=rr_ratio)

    all_trades = []
    total_pnl = 0.0
    wins = 0
    losses = 0
    gross_profits = 0.0
    gross_losses = 0.0

    window_size = max(50, swing_lookback * 4 + 10)
    i = window_size
    in_trade = False
    trade_entry = 0.0
    trade_sl = 0.0
    trade_tp = 0.0
    trade_direction = ""
    trade_entry_time = ""

    while i < len(df):
        if not in_trade:
            window = df.iloc[max(0, i - window_size):i + 1].copy()
            signal = tracker.detect_bos(window)

            if signal:
                in_trade = True
                trade_entry = signal.entry_price
                trade_sl = signal.stop_loss
                trade_tp = signal.take_profit
                trade_direction = signal.direction
                trade_entry_time = df.index[i].strftime("%Y-%m-%d %H:%M")
            i += 1
            continue
        else:
            candle = df.iloc[i]
            high = float(candle['High'])
            low = float(candle['Low'])

            pnl = 0.0
            exit_reason = ""
            exit_price = 0.0

            if trade_direction == "BULLISH":
                if low <= trade_sl:
                    pnl = -risk_dollars
                    exit_price = trade_sl
                    exit_reason = "SL"
                elif high >= trade_tp:
                    pnl = risk_dollars * rr_ratio
                    exit_price = trade_tp
                    exit_reason = "TP"
            else:
                if high >= trade_sl:
                    pnl = -risk_dollars
                    exit_price = trade_sl
                    exit_reason = "SL"
                elif low <= trade_tp:
                    pnl = risk_dollars * rr_ratio
                    exit_price = trade_tp
                    exit_reason = "TP"

            if exit_reason:
                trade = {
                    "date": trade_entry_time,
                    "symbol": "GOLD",
                    "side": trade_direction,
                    "entry_price": round(trade_entry, 2),
                    "exit_price": round(exit_price, 2),
                    "exit_time": df.index[i].strftime("%Y-%m-%d %H:%M"),
                    "pnl": round(pnl, 2),
                    "result": exit_reason,
                }
                all_trades.append(trade)
                total_pnl += pnl
                if pnl >= 0:
                    wins += 1
                    gross_profits += pnl
                else:
                    losses += 1
                    gross_losses += abs(pnl)
                in_trade = False
            i += 1

    total_trades = wins + losses
    win_rate = round((wins / total_trades) * 100, 1) if total_trades > 0 else 0.0
    profit_factor = round(gross_profits / gross_losses, 2) if gross_losses > 0 else (
        round(gross_profits, 2) if gross_profits > 0 else 0.0
    )

    all_trades.sort(key=lambda x: x["date"], reverse=True)

    return {
        "status": "success",
        "summary": {
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "win_trades": wins,
            "loss_trades": losses,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
        },
        "trades": all_trades,
    }
