import logging
import pytz
from typing import Any, Optional
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


def run_orb_backtest(tickers: list, start_date_str: str, end_date_str: str, risk_dollars: float = 10.0, rr_ratio: float = 1.5) -> dict:
    """
    Simulates historical ORB breakout strategy triggers using 5-minute wicks from yfinance.
    """
    import yfinance as yf
    import pandas as pd
    import pytz
    from datetime import datetime, timedelta
    
    # Parse dates
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    except Exception as e:
        return {"status": "error", "message": f"Invalid date format: {e}"}
        
    # Enforce last 60 days limit for 5m interval
    limit_date = datetime.now() - timedelta(days=60)
    if start_date < limit_date:
        start_date = limit_date
        start_date_str = start_date.strftime("%Y-%m-%d")
        
    tomorrow = end_date + timedelta(days=1)
    end_date_str_excl = tomorrow.strftime("%Y-%m-%d")
    
    all_trades = []
    summary = {
        "total_pnl": 0.0,
        "total_trades": 0,
        "win_trades": 0,
        "loss_trades": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
    }
    
    gross_profits = 0.0
    gross_losses = 0.0
    
    eastern = pytz.timezone("US/Eastern")
    
    for ticker in tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue
        try:
            df = yf.download(ticker, start=start_date_str, end=end_date_str_excl, interval="5m", progress=False)
            if df.empty:
                continue
                
            # If MultiIndex columns exist, flatten them
            if isinstance(df.columns, pd.MultiIndex):
                if ticker in df.columns.levels[0]:
                    df = df[ticker].dropna()
                else:
                    df = df.xs(ticker, level=1, axis=1).dropna()
            
            # Localize index
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(eastern)
            else:
                df.index = df.index.tz_convert(eastern)
                
            # Group by day
            grouped = df.groupby(df.index.date)
            for date, day_df in grouped:
                # 9:30 AM to 9:45 AM EST (exclusive of 9:45)
                orb_df = day_df.between_time("09:30", "09:44")
                if orb_df.empty:
                    continue
                    
                orb_high = float(orb_df["High"].max())
                orb_low = float(orb_df["Low"].min())
                orb_mid = (orb_high + orb_low) / 2.0
                
                # Trading window: 9:45 AM to 3:55 PM EST
                trade_df = day_df.between_time("09:45", "15:55")
                if trade_df.empty:
                    continue
                    
                position = None
                entry_price = 0.0
                stop_loss = 0.0
                take_profit = 0.0
                pnl = 0.0
                entry_time = None
                exit_time = None
                exit_price = 0.0
                exit_reason = ""
                
                for timestamp, row in trade_df.iterrows():
                    high = float(row["High"])
                    low = float(row["Low"])
                    
                    if position is None:
                        # Breakout check
                        if high >= orb_high:
                            position = "Long"
                            entry_price = orb_high
                            stop_loss = orb_mid
                            take_profit = entry_price + rr_ratio * (entry_price - stop_loss)
                            entry_time = timestamp.strftime("%H:%M")
                        elif low <= orb_low:
                            position = "Short"
                            entry_price = orb_low
                            stop_loss = orb_mid
                            take_profit = entry_price - rr_ratio * (stop_loss - entry_price)
                            entry_time = timestamp.strftime("%H:%M")
                    else:
                        # Monitor position
                        if position == "Long":
                            if low <= stop_loss:
                                pnl = -risk_dollars
                                exit_price = stop_loss
                                exit_reason = "SL"
                                exit_time = timestamp.strftime("%H:%M")
                                break
                            elif high >= take_profit:
                                pnl = risk_dollars * rr_ratio
                                exit_price = take_profit
                                exit_reason = "TP"
                                exit_time = timestamp.strftime("%H:%M")
                                break
                        elif position == "Short":
                            if high >= stop_loss:
                                pnl = -risk_dollars
                                exit_price = stop_loss
                                exit_reason = "SL"
                                exit_time = timestamp.strftime("%H:%M")
                                break
                            elif low <= take_profit:
                                pnl = risk_dollars * rr_ratio
                                exit_price = take_profit
                                exit_reason = "TP"
                                exit_time = timestamp.strftime("%H:%M")
                                break
                else:
                    # End of day closeout if trade still open
                    if position is not None:
                        final_close = float(trade_df.iloc[-1]["Close"])
                        exit_price = final_close
                        exit_reason = "EOD"
                        exit_time = trade_df.index[-1].strftime("%H:%M")
                        
                        price_diff = abs(entry_price - stop_loss)
                        if price_diff > 0:
                            qty = risk_dollars / price_diff
                            if position == "Long":
                                pnl = (final_close - entry_price) * qty
                            else:
                                pnl = (entry_price - final_close) * qty
                        else:
                            pnl = 0.0
                            
                if position is not None:
                    trade_result = {
                        "date": date.strftime("%Y-%m-%d"),
                        "symbol": ticker,
                        "side": position,
                        "entry_time": entry_time,
                        "entry_price": round(entry_price, 2),
                        "exit_time": exit_time,
                        "exit_price": round(exit_price, 2),
                        "pnl": round(pnl, 2),
                        "result": exit_reason
                    }
                    all_trades.append(trade_result)
                    
                    summary["total_pnl"] += pnl
                    summary["total_trades"] += 1
                    if pnl >= 0:
                        summary["win_trades"] += 1
                        gross_profits += pnl
                    else:
                        summary["loss_trades"] += 1
                        gross_losses += abs(pnl)
        except Exception as ex:
            print(f"Error backtesting {ticker}: {ex}")
            continue
            
    # Calculate stats
    total_t = summary["total_trades"]
    if total_t > 0:
        summary["win_rate"] = round((summary["win_trades"] / total_t) * 100, 1)
    summary["total_pnl"] = round(summary["total_pnl"], 2)
    if gross_losses > 0:
        summary["profit_factor"] = round(gross_profits / gross_losses, 2)
    else:
        summary["profit_factor"] = round(gross_profits, 2) if gross_profits > 0 else 1.0
        
    # Sort trades by date (descending)
    all_trades.sort(key=lambda x: x["date"], reverse=True)
    
    return {
        "status": "success",
        "summary": summary,
        "trades": all_trades
    }

