#!/usr/bin/env python3
"""
China A-share trading-day guard for generated reports.

The 2026 holiday list is based on Shanghai Stock Exchange official holiday
announcements. Weekends are treated as closed automatically.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass


SSE_2026_HOLIDAY_SOURCE = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
SSE_2026_NOTICE_SOURCE = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
SSE_2026_DRAGON_BOAT_SOURCE = "https://www.sse.com.cn/disclosure/announcement/general/c/c_20260611_10821419.shtml"


@dataclass(frozen=True)
class TradingDayStatus:
    date: dt.date
    is_open: bool
    reason: str
    source: str | None = None


CN_A_SHARE_HOLIDAYS_2026: dict[dt.date, str] = {
    # SSE annual holiday notice: 2026-01-01 to 2026-01-03; Jan 3 is weekend.
    dt.date(2026, 1, 1): "元旦休市",
    dt.date(2026, 1, 2): "元旦休市",
    # SSE annual holiday notice: 2026-02-15 to 2026-02-23; Feb 15 is weekend.
    dt.date(2026, 2, 16): "春节休市",
    dt.date(2026, 2, 17): "春节休市",
    dt.date(2026, 2, 18): "春节休市",
    dt.date(2026, 2, 19): "春节休市",
    dt.date(2026, 2, 20): "春节休市",
    dt.date(2026, 2, 23): "春节休市",
    # SSE annual holiday notice: 2026-04-04 to 2026-04-06; Apr 4-5 are weekend.
    dt.date(2026, 4, 6): "清明节休市",
    # SSE annual holiday notice: 2026-05-01 to 2026-05-05; May 2-3 are weekend.
    dt.date(2026, 5, 1): "劳动节休市",
    dt.date(2026, 5, 4): "劳动节休市",
    dt.date(2026, 5, 5): "劳动节休市",
    # SSE Dragon Boat Festival notice: 2026-06-19 to 2026-06-21; Jun 20-21 are weekend.
    dt.date(2026, 6, 19): "端午节休市",
    # SSE annual holiday notice: 2026-09-25 to 2026-09-27; Sep 26-27 are weekend.
    dt.date(2026, 9, 25): "中秋节休市",
    # SSE annual holiday notice: 2026-10-01 to 2026-10-07; Oct 3-4 are weekend.
    dt.date(2026, 10, 1): "国庆节休市",
    dt.date(2026, 10, 2): "国庆节休市",
    dt.date(2026, 10, 5): "国庆节休市",
    dt.date(2026, 10, 6): "国庆节休市",
    dt.date(2026, 10, 7): "国庆节休市",
}


def parse_date(value: str | None) -> dt.date:
    if not value:
        return dt.datetime.now().date()
    return dt.date.fromisoformat(value)


def a_share_trading_day_status(day: dt.date) -> TradingDayStatus:
    if day.weekday() >= 5:
        return TradingDayStatus(day, False, "周末休市", SSE_2026_HOLIDAY_SOURCE if day.year == 2026 else None)
    if day.year == 2026 and day in CN_A_SHARE_HOLIDAYS_2026:
        source = SSE_2026_DRAGON_BOAT_SOURCE if day == dt.date(2026, 6, 19) else SSE_2026_NOTICE_SOURCE
        return TradingDayStatus(day, False, CN_A_SHARE_HOLIDAYS_2026[day], source)
    return TradingDayStatus(day, True, "交易日")


def format_status(status: TradingDayStatus) -> str:
    state = "OPEN" if status.is_open else "CLOSED"
    suffix = f" source={status.source}" if status.source else ""
    return f"{state} {status.date.isoformat()} {status.reason}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether mainland China A-share market is open.")
    parser.add_argument("--date", default=None, help="Date to check, YYYY-MM-DD. Default: today.")
    parser.add_argument("--quiet", action="store_true", help="Suppress status output.")
    parser.add_argument(
        "--closed-exit-code",
        type=int,
        default=2,
        help="Process exit code when the market is closed. Default: 2.",
    )
    args = parser.parse_args()

    status = a_share_trading_day_status(parse_date(args.date))
    if not args.quiet:
        print(format_status(status))
    return 0 if status.is_open else args.closed_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
