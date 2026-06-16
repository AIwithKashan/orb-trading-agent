import unittest
import pytz
import pandas as pd
from datetime import datetime
from strategy import ORBTracker

class DummyBar:
    """A dummy bar object to mimic API bar entities."""
    def __init__(self, timestamp: datetime, high: float, low: float) -> None:
        self.timestamp = timestamp
        self.high = high
        self.low = low
        
    @property
    def t(self) -> datetime:
        return self.timestamp
        
    @property
    def h(self) -> float:
        return self.high
        
    @property
    def l(self) -> float:
        return self.low

class TestORBTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = ORBTracker("AAPL")
        
    def test_calculate_orb_levels_list_of_bars(self) -> None:
        eastern = pytz.timezone('US/Eastern')
        # Create timestamps representing timezone-aware datetimes
        dt_930 = eastern.localize(datetime(2026, 6, 13, 9, 30))
        dt_935 = eastern.localize(datetime(2026, 6, 13, 9, 35))
        
        bars = [
            DummyBar(dt_930, 150.0, 148.0),
            DummyBar(dt_935, 151.0, 149.0),
        ]
        
        self.tracker.calculate_orb_levels(bars)
        self.assertEqual(self.tracker.orb_high, 150.0)
        self.assertEqual(self.tracker.orb_low, 148.0)
        self.assertEqual(self.tracker.orb_mid, 149.0)

    def test_calculate_orb_levels_dataframe(self) -> None:
        eastern = pytz.timezone('US/Eastern')
        dt_930 = eastern.localize(datetime(2026, 6, 13, 9, 30))
        dt_935 = eastern.localize(datetime(2026, 6, 13, 9, 35))
        
        data = {
            'high': [152.0, 153.0],
            'low': [150.0, 151.0]
        }
        df = pd.DataFrame(data, index=[dt_930, dt_935])
        
        self.tracker.calculate_orb_levels(df)
        self.assertEqual(self.tracker.orb_high, 152.0)
        self.assertEqual(self.tracker.orb_low, 150.0)
        self.assertEqual(self.tracker.orb_mid, 151.0)

    def test_calculate_orb_levels_not_found(self) -> None:
        eastern = pytz.timezone('US/Eastern')
        # 8:00 AM is before the 9:30 target — should NOT match
        dt_0800 = eastern.localize(datetime(2026, 6, 13, 8, 0))
        
        bars = [
            DummyBar(dt_0800, 150.0, 148.0)
        ]
        with self.assertRaises(ValueError):
            self.tracker.calculate_orb_levels(bars)

    def test_calculate_position_size(self) -> None:
        # Risk = $10. Entry = 100. Stop = 98. Diff = 2. Qty = 10 / 2 = 5.0
        qty = self.tracker.calculate_position_size(100.0, 98.0)
        self.assertEqual(qty, 5.0)
        
        # Risk = $10. Entry = 100. Stop = 102. Diff = 2. Qty = 10 / 2 = 5.0
        qty2 = self.tracker.calculate_position_size(100.0, 102.0)
        self.assertEqual(qty2, 5.0)

    def test_calculate_position_size_zero_division(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            self.tracker.calculate_position_size(100.0, 100.0)

if __name__ == '__main__':
    unittest.main()
