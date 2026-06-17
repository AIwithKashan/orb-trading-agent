import logging
import pytz
from typing import Any
from datetime import datetime

# Setup logging
logger = logging.getLogger("strategy")

class ORBTracker:
    """
    Open Range Breakout (ORB) Strategy Tracker for US Stocks.
    
    Tracks a specific stock symbol and calculates trading boundaries based on
    the opening trading range (the first 5-minute window, 9:30 AM - 9:35 AM EST).
    
    Attributes:
        symbol (str): The asset ticker symbol (e.g., 'AAPL').
        orb_high (float): The high price of the opening range (Green line).
        orb_low (float): The low price of the opening range (Red line).
        orb_mid (float): The mid-point of the opening range (White line / stop loss).
        was_inside_range (bool): Whether price was observed inside the ORB range.
    """
    
    TIMEZONE_EST = pytz.timezone('US/Eastern')
    TARGET_HOUR = 9
    TARGET_MINUTE = 30
    
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self.orb_high: float = 0.0
        self.orb_low: float = 0.0
        self.orb_mid: float = 0.0
        self.was_inside_range: bool = False

    def calculate_orb_levels(self, historical_5m_bars: Any) -> None:
        """
        Finds the 5-minute bars between 9:30 AM and 9:45 AM EST,
        determines the highest high and lowest low of the range,
        and computes the mid-point.
        
        Parameters:
            historical_5m_bars (Any): A list of bar objects or a pandas DataFrame.
                                      
        Raises:
            ValueError: If the opening bars cannot be found.
        """
        target_tz = self.TIMEZONE_EST
        start_minutes = 9 * 60 + 30
        end_minutes = 9 * 60 + 45
        
        import pandas as pd
        highs = []
        lows = []

        # Extract pandas DataFrame if it is a BarsV2 object
        if hasattr(historical_5m_bars, 'df'):
            historical_5m_bars = historical_5m_bars.df

        # Case 1: pandas DataFrame
        if hasattr(historical_5m_bars, 'iterrows') and not historical_5m_bars.empty:
            df_sorted = historical_5m_bars.sort_index()
            for timestamp, row in df_sorted.iterrows():
                if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo is not None:
                    dt_target = timestamp.astimezone(target_tz)
                else:
                    dt_target = pytz.utc.localize(timestamp).astimezone(target_tz)
                
                bar_minutes = dt_target.hour * 60 + dt_target.minute
                if start_minutes <= bar_minutes < end_minutes:
                    highs.append(float(row.get('high', row.get('High', 0.0))))
                    lows.append(float(row.get('low', row.get('Low', 0.0))))
        elif not hasattr(historical_5m_bars, 'iterrows') and historical_5m_bars:
            # Case 2: List of Bar objects or dictionaries
            bars_with_time = []
            for bar in historical_5m_bars:
                raw_t = getattr(bar, 't', getattr(bar, 'timestamp', None))
                if raw_t is None and isinstance(bar, dict):
                    raw_t = bar.get('t', bar.get('timestamp'))
                if raw_t is not None:
                    bars_with_time.append((raw_t, bar))
            
            bars_with_time.sort(key=lambda x: pd.to_datetime(x[0]) if isinstance(x[0], str) else x[0])
            
            for raw_t, bar in bars_with_time:
                if isinstance(raw_t, str):
                    try:
                        dt = pd.to_datetime(raw_t).to_pydatetime()
                    except Exception:
                        continue
                elif isinstance(raw_t, (int, float)):
                    dt = datetime.fromtimestamp(raw_t, tz=pytz.utc)
                else:
                    dt = raw_t

                if dt.tzinfo is None:
                    dt = pytz.utc.localize(dt)
                    
                dt_target = dt.astimezone(target_tz)
                bar_minutes = dt_target.hour * 60 + dt_target.minute
                if start_minutes <= bar_minutes < end_minutes:
                    highs.append(float(getattr(bar, 'h', getattr(bar, 'high', 0.0))))
                    lows.append(float(getattr(bar, 'l', getattr(bar, 'low', 0.0))))

        if not highs or not lows:
            raise ValueError(
                f"Could not locate any 5-minute bars between 09:30 and 09:45 EST for symbol {self.symbol}."
            )

        self.orb_high = max(highs)
        self.orb_low = min(lows)
        self.orb_mid = (self.orb_high + self.orb_low) / 2.0
        self.was_inside_range = False
        
        logger.info(
            f"15-Min ORB levels for {self.symbol}: High = ${self.orb_high:.4f}, "
            f"Low = ${self.orb_low:.4f}, Mid = ${self.orb_mid:.4f}"
        )

    def calculate_position_size(self, entry_price: float, stop_loss_price: float, 
                                equity: float = 500.0, risk_pct: float = 2.0,
                                risk_dollars: Optional[float] = None) -> float:
        """
        Determines the position size based on a percentage of account equity or flat risk.
        
        Parameters:
            entry_price (float): The entry execution price.
            stop_loss_price (float): The stop loss trigger price.
            equity (float): The account equity to use.
            risk_pct (float): The percentage of equity to risk per trade.
            risk_dollars (float, optional): Flat dollar risk amount.
            
        Returns:
            float: The calculated quantity to trade.
        """
        price_diff = abs(entry_price - stop_loss_price)
        if price_diff == 0.0:
            raise ZeroDivisionError("Entry price and Stop Loss price cannot be equal.")
            
        if risk_dollars is not None and risk_dollars > 0:
            dollar_risk = risk_dollars
        else:
            dollar_risk = equity * (risk_pct / 100.0)
            
        qty = dollar_risk / price_diff
        return qty
