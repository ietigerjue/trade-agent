"""Tests for US daily agent and supporting modules."""

import datetime as dt
import unittest

from trading_strategy import Bar

from us_market_data import (
    build_us_quote,
    load_us_universe,
    DEFAULT_US_UNIVERSE,
)
from us_trading_calendar import us_trading_day_status


class USMarketDataTests(unittest.TestCase):
    """Tests for us_market_data.py utilities."""

    def test_build_us_quote_basic(self) -> None:
        bars = [
            Bar(date="2026-07-01", open=300.0, high=310.0, low=298.0,
                close=305.0, volume=10_000_000.0),
            Bar(date="2026-07-02", open=305.0, high=315.0, low=303.0,
                close=308.63, volume=12_000_000.0),
        ]
        quote = build_us_quote("AAPL", bars)
        self.assertEqual(quote["code"], "AAPL")
        self.assertEqual(quote["name"], "Apple")
        # change% = (308.63 - 305.0) / 305.0 * 100 = 1.19%
        self.assertAlmostEqual(quote["changepercent"], 1.19, places=1)
        # amount = 308.63 * 12_000_000 = ~3.7B
        self.assertGreater(quote["amount"], 1_000_000_000)

    def test_build_us_quote_single_bar(self) -> None:
        bars = [
            Bar(date="2026-07-02", open=100.0, high=105.0, low=99.0,
                close=102.0, volume=5_000_000.0),
        ]
        quote = build_us_quote("TEST", bars)
        self.assertEqual(quote["changepercent"], 0.0)

    def test_build_us_quote_unknown_symbol(self) -> None:
        bars = [
            Bar(date="2026-07-01", open=10.0, high=11.0, low=9.0,
                close=10.5, volume=1_000.0),
            Bar(date="2026-07-02", open=10.5, high=11.5, low=10.0,
                close=11.0, volume=2_000.0),
        ]
        quote = build_us_quote("ZZZZ", bars)
        self.assertEqual(quote["name"], "ZZZZ")  # fallback to symbol

    def test_load_us_universe_default(self) -> None:
        tickers = load_us_universe(None)
        self.assertGreater(len(tickers), 50)
        self.assertIn("AAPL", tickers)

    def test_load_us_universe_nonexistent_file(self) -> None:
        tickers = load_us_universe("nonexistent_file.txt")
        self.assertEqual(tickers, DEFAULT_US_UNIVERSE)


class USTradingCalendarTests(unittest.TestCase):
    """Tests for us_trading_calendar.py."""

    def test_weekend_saturday(self) -> None:
        # 2026-07-04 is a Saturday
        status = us_trading_day_status(dt.date(2026, 7, 4))
        self.assertFalse(status.is_open)
        self.assertIn("周末", status.reason)

    def test_holiday_closed(self) -> None:
        # 2026-07-03 is Independence Day (observed)
        status = us_trading_day_status(dt.date(2026, 7, 3))
        self.assertFalse(status.is_open)

    def test_weekday_open(self) -> None:
        # 2026-07-06 is a Monday, no holiday
        status = us_trading_day_status(dt.date(2026, 7, 6))
        self.assertTrue(status.is_open)

    def test_christmas_closed(self) -> None:
        status = us_trading_day_status(dt.date(2026, 12, 25))
        self.assertFalse(status.is_open)

    def test_thanksgiving_closed(self) -> None:
        status = us_trading_day_status(dt.date(2026, 11, 26))
        self.assertFalse(status.is_open)


class USEnrichmentTests(unittest.TestCase):
    """Tests for US-specific enrichment in us_daily_agent.py."""

    def test_enrich_us_candidate_null_sector(self) -> None:
        from us_daily_agent import enrich_us_candidate
        from a_share_daily_agent import AShareCandidate

        c = AShareCandidate(
            code="AAPL", name="Apple", date="2026-07-02",
            close=300.0, pct_change=1.5,
            amount=1e10, setup="突破后回踩",
            signals=["bullish pinbar"],
            reward_risk=2.5, reward_risk_confidence=0.8,
            final_score=50.0,
            bullish_confidence=70, bearish_confidence=25,
            confidence_factors=["MA支撑"],
            bearish_factors=[],
            support=295.0, support_date="2026-06-20",
            target=320.0, target_date="2026-06-25",
            stop=290.0,
            ma5=298.0, ma10=296.0, ma20=294.0, ma30=292.0, ma60=285.0,
            gain_30=3.0, gain_60=20.0, velocity_30=0.1,
            buy_sell_ratio_60=2.0,
            volume_ratio=1.2, false_breaks=0,
            industry=None, concepts=None, community=None, latest_note=None,
        )
        enriched = enrich_us_candidate(c)
        self.assertIsNone(enriched.industry)
        self.assertIsNone(enriched.concepts)
        self.assertIsNone(enriched.community)
        self.assertIsNone(enriched.latest_note)
        # final_score should be recomputed
        self.assertNotEqual(enriched.final_score, 50.0)

    def test_brooks_price_action_section_surfaces_us_factors(self) -> None:
        from us_daily_agent import build_brooks_price_action_section
        from a_share_daily_agent import AShareCandidate

        c = AShareCandidate(
            code="NVDA", name="NVIDIA", date="2026-07-07",
            close=180.0, pct_change=1.2,
            amount=5e10, setup="突破后回踩",
            signals=["回踩不破"],
            reward_risk=2.2, reward_risk_confidence=0.78,
            final_score=90.0,
            bullish_confidence=82, bearish_confidence=28,
            confidence_factors=["强趋势背景", "三推楔形牛旗:push递减10.0%/6.0%"],
            bearish_factors=["重叠K较多/交易区间倾向"],
            support=175.0, support_date="2026-07-01",
            target=198.0, target_date="2026-06-20",
            stop=171.0,
            ma5=181.0, ma10=178.0, ma20=174.0, ma30=170.0, ma60=158.0,
            gain_30=12.0, gain_60=25.0, velocity_30=0.4,
            buy_sell_ratio_60=2.1,
            volume_ratio=1.4, false_breaks=0,
            industry=None, concepts=None, community=None, latest_note=None,
        )

        section = build_brooks_price_action_section([c], [])

        self.assertIn("Al Brooks价格行为观察", section)
        self.assertIn("NVDA NVIDIA", section)
        self.assertIn("三推楔形牛旗", section)
        self.assertIn("重叠K", section)


class CombinedReportTests(unittest.TestCase):
    """Tests for combined_report_builder.py."""

    def test_build_combined_report_structure(self) -> None:
        from combined_report_builder import build_combined_report
        from pathlib import Path
        import tempfile, os

        # Create temporary A-share and US report files
        tmpdir = tempfile.mkdtemp()
        try:
            a_path = Path(tmpdir) / "a_share_latest.md"
            us_path = Path(tmpdir) / "us_latest.md"
            a_path.write_text(
                "# A股报告\n\n## 大盘环境\n\n上证涨了\n",
                encoding="utf-8",
            )
            us_path.write_text(
                "# 美股报告\n\n## 做多候选\n\nAAPL\n",
                encoding="utf-8",
            )

            report = build_combined_report(
                a_share_report_path=a_path,
                us_report_path=us_path,
                a_share_date="2026-07-02",
                us_date="2026-07-02",
                a_share_open=True,
                us_open=True,
                a_share_candidates=3,
                us_candidates=2,
                us_trend=1,
                us_range=0,
            )

            self.assertIn("多市场裸K做多观察报告", report)
            self.assertIn("A股部分 (2026-07-02)", report)
            # US section content should include the body (H1 gets stripped by _extract_body)
            self.assertIn("大盘环境", report)
            self.assertIn("做多候选", report)
            self.assertIn("AAPL", report)
            self.assertIn("美股收盘于美东时间", report)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_extract_body_removes_h1(self) -> None:
        from combined_report_builder import _extract_body

        md = "# Title\n\n## Section\n\ncontent\n"
        body = _extract_body(md)
        self.assertNotIn("# Title", body)
        self.assertIn("## Section", body)

    def test_extract_body_preserves_h2(self) -> None:
        from combined_report_builder import _extract_body

        md = "## Subtitle\n\ncontent\n"
        body = _extract_body(md)
        self.assertIn("## Subtitle", body)


if __name__ == "__main__":
    unittest.main()
