import datetime as dt
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from a_share_trading_calendar import a_share_trading_day_status  # noqa: E402


class AShareTradingCalendarTests(unittest.TestCase):
    def test_dragon_boat_2026_is_closed(self) -> None:
        status = a_share_trading_day_status(dt.date(2026, 6, 19))

        self.assertFalse(status.is_open)
        self.assertIn("端午节", status.reason)

    def test_regular_weekday_after_dragon_boat_is_open(self) -> None:
        status = a_share_trading_day_status(dt.date(2026, 6, 22))

        self.assertTrue(status.is_open)

    def test_weekend_is_closed(self) -> None:
        status = a_share_trading_day_status(dt.date(2026, 6, 20))

        self.assertFalse(status.is_open)
        self.assertEqual(status.reason, "周末休市")


if __name__ == "__main__":
    unittest.main(verbosity=2)
