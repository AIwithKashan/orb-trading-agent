import logging
from typing import Optional, Any
import alpaca_trade_api as tradeapi
from alpaca_trade_api.rest import APIError

logger = logging.getLogger("broker")

class AlpacaBroker:
    """
    Broker interface for the Alpaca Trade API.
    Accepts API keys as constructor parameters for multi-user support.
    """
    
    def __init__(self, api_key: str, secret_key: str, 
                 base_url: str = "https://paper-api.alpaca.markets") -> None:
        if not api_key or not secret_key:
            raise ValueError("Alpaca API key and secret key are required.")
            
        self.api = tradeapi.REST(
            key_id=api_key,
            secret_key=secret_key,
            base_url=base_url
        )

    def get_account_equity(self) -> float:
        """Fetches the account equity from Alpaca."""
        try:
            account = self.api.get_account()
            return float(account.equity)
        except Exception as e:
            logger.error(f"Error fetching account equity: {e}")
            return 0.0

    def get_latest_prices(self, symbols: list) -> dict:
        """Fetches the latest trade prices for a list of stock symbols in batch."""
        prices = {}
        if not symbols:
            return prices
            
        try:
            cleaned = [sym.upper() for sym in symbols]
            try:
                trades = self.api.get_latest_trades(cleaned, feed="sip")
            except Exception as e:
                if "sip" in str(e).lower():
                    trades = self.api.get_latest_trades(cleaned, feed="iex")
                else:
                    raise e
            for symbol, trade in trades.items():
                prices[symbol] = float(trade.price)
            return prices
        except Exception as e:
            logger.error(f"Alpaca API error fetching batch prices: {e}")
            return prices

    def submit_bracket_order(
        self, symbol: str, qty: float, side: str,
        take_profit_price: float, stop_loss_price: float,
        order_type: str = 'market', time_in_force: str = 'day'
    ) -> Optional[Any]:
        """Submits a bracket order with take-profit and stop-loss legs."""
        try:
            side_lower = side.lower()
            if side_lower not in ['buy', 'sell']:
                raise ValueError("Order side must be 'buy' or 'sell'")
            
            # Round to 2 decimal places to satisfy Alpaca minimum pricing criteria (avoid sub-penny errors)
            take_profit_price = round(float(take_profit_price), 2)
            stop_loss_price = round(float(stop_loss_price), 2)
            
            order_params = {
                "symbol": symbol.upper(),
                "qty": qty,
                "side": side_lower,
                "type": order_type.lower(),
                "time_in_force": time_in_force.lower(),
                "order_class": "bracket",
                "take_profit": {"limit_price": str(take_profit_price)},
                "stop_loss": {"stop_price": str(stop_loss_price)}
            }
            
            order = self.api.submit_order(**order_params)
            logger.info(f"Bracket order submitted. ID: {order.id}")
            return order
            
        except APIError as e:
            logger.error(f"Alpaca API error submitting order for {symbol}: {e}")
        except Exception as e:
            logger.error(f"Error submitting order for {symbol}: {e}")
        return None

    def cancel_all_orders_and_close_positions(self) -> bool:
        """Cancels all orders and closes all positions (EOD liquidation)."""
        success = True
        try:
            self.api.cancel_all_orders()
            logger.info("All open orders cancelled.")
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
            success = False
        try:
            self.api.close_all_positions()
            logger.info("All positions closed.")
        except Exception as e:
            logger.error(f"Error closing positions: {e}")
            success = False
        return success
