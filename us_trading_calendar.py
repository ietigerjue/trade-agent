#!/usr/bin/env python3
"""US stock market trading-day guard.

Checks whether the US market was open on a given date.
Weekends are always closed. NYSE holidays for 2026 are hardcoded.

Default date is YESTERDAY (US market close at 16:00 ET = 04:00 HKT next day,
so the "daily" report always analyzes the previous calendar day).
Exits with code 2 when the market is closed (for PowerShell gating).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class TradingDayStatus:
    date: dt.date
    is_open: bool
    reason: str
    source: str | None = None


# NYSE 2026 holiday calendar (source: NYSE official)
US_HOLIDAYS_2026: dict[dt.date, str] = {
    dt.date(2026, 1, 1): "New Year's Day",
    dt.date(2026, 1, 19): "Martin Luther King Jr. Day",
    dt.date(2026, 2, 16): "Presidents' Day",
    dt.date(2026, 4, 3): "Good Friday",
    dt.date(2026, 5, 25): "Memorial Day",
    dt.date(2026, 6, 19): "Juneteenth National Independence Day",
    dt.date(2026, 7, 3): "Independence Day (observed — Jul 4 is Saturday)",
    dt.date(2026, 9, 7): "Labor Day",
    dt.date(2026, 11, 26): "Thanksgiving Day",
    dt.date(2026, 12, 25): "Christmas Day",
}

_HOLIDAY_SOURCE = "https://www.nyse.com/markets/hours-calendars"


def us_trading_day_status(day: dt.date) -> TradingDayStatus:
    """Check if US stock market was open on the given date."""
    if day.weekday() >= 5:  # Saturday=5, Sunday=6
        return TradingDayStatus(day, False, "周末休市", _HOLIDAY_SOURCE)
    if day.year == 2026 and day in US_HOLIDAYS_2026:
        return TradingDayStatus(
            day, False, US_HOLIDAYS_2026[day], _HOLIDAY_SOURCE
        )
    return TradingDayStatus(day, True, "交易日")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check US market trading day status"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date to check (YYYY-MM-DD). Default: yesterday (US time).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="No output, just exit code"
    )
    parser.add_argument(
        "--closed-exit-code",
        type=int,
        default=2,
        help="Exit code when market is closed (default: 2)",
    )
    args = parser.parse_args()

    if args.date:
        day = dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        # Default: yesterday (US market data available next HKT morning)
        day = dt.date.today() - dt.timedelta(days=1)

    status = us_trading_day_status(day)

    if not args.quiet:
        tag = "OPEN" if status.is_open else "CLOSED"
        print(f"{day} US Market: {tag} — {status.reason}")

    if not status.is_open:
        sys.exit(args.closed_exit_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
