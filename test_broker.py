import unittest
from unittest.mock import MagicMock, patch
import broker

class TestAlpacaBroker(unittest.TestCase):
    @patch('broker.config')
    @patch('broker.tradeapi.REST')
    def setUp(self, mock_rest_class, mock_config):
        # 1. Setup mock configuration
        mock_config.ALPACA_API_KEY = "dummy_key"
        mock_config.ALPACA_SECRET_KEY = "dummy_secret"
        mock_config.ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
        
        # 2. Setup mock REST client instance
        self.mock_api = MagicMock()
        mock_rest_class.return_value = self.mock_api
        
        # 3. Instantiate the broker
        self.broker = broker.AlpacaBroker()

    def test_get_current_price_success(self):
        # Mock the get_latest_trade output
        mock_trade = MagicMock()
        mock_trade.price = 150.50
        self.mock_api.get_latest_trade.return_value = mock_trade
        
        price = self.broker.get_current_price("AAPL")
        self.assertEqual(price, 150.50)
        self.mock_api.get_latest_trade.assert_called_once_with("AAPL")

    def test_get_current_price_error(self):
        # Mock an API error/exception
        self.mock_api.get_latest_trade.side_effect = Exception("API connection error")
        price = self.broker.get_current_price("AAPL")
        self.assertIsNone(price)

    def test_submit_bracket_order_market_success(self):
        # Mock submission return
        mock_order = MagicMock()
        mock_order.id = "order-id-123"
        self.mock_api.submit_order.return_value = mock_order
        
        order = self.broker.submit_bracket_order(
            symbol="AAPL",
            qty=10,
            side="buy",
            take_profit_price=160.0,
            stop_loss_price=140.0
        )
        
        self.mock_api.submit_order.assert_called_once_with(
            symbol="AAPL",
            qty=10.0,
            side="buy",
            type="market",
            time_in_force="day",
            order_class="bracket",
            take_profit={"limit_price": "160.0"},
            stop_loss={"stop_price": "140.0"}
        )
        self.assertEqual(order.id, "order-id-123")

    def test_submit_bracket_order_limit_success(self):
        # Mock submission return
        mock_order = MagicMock()
        mock_order.id = "order-id-456"
        self.mock_api.submit_order.return_value = mock_order
        
        order = self.broker.submit_bracket_order(
            symbol="AAPL",
            qty=10,
            side="buy",
            take_profit_price=160.0,
            stop_loss_price=140.0,
            order_type="limit",
            limit_price=150.0
        )
        
        self.mock_api.submit_order.assert_called_once_with(
            symbol="AAPL",
            qty=10.0,
            side="buy",
            type="limit",
            time_in_force="day",
            order_class="bracket",
            take_profit={"limit_price": "160.0"},
            stop_loss={"stop_price": "140.0"},
            limit_price="150.0"
        )
        self.assertEqual(order.id, "order-id-456")

    def test_cancel_all_orders_and_close_positions_success(self):
        # Test clean execution path
        success = self.broker.cancel_all_orders_and_close_positions()
        self.assertTrue(success)
        self.mock_api.cancel_all_orders.assert_called_once()
        self.mock_api.close_all_positions.assert_called_once()

    def test_cancel_all_orders_and_close_positions_error(self):
        # Test failure path
        self.mock_api.cancel_all_orders.side_effect = Exception("Network connection lost")
        success = self.broker.cancel_all_orders_and_close_positions()
        self.assertFalse(success)

if __name__ == '__main__':
    unittest.main()
