from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import a_share_daily_agent as agent  # noqa: E402
from a_share_daily_agent import Bar, RangeBoundCandidate, build_report  # noqa: E402


class AShareDailyRegressionTests(unittest.TestCase):
    @staticmethod
    def _descending_channel_bars() -> list[Bar]:
        bars = [
            Bar(
                date=f"d{index}",
                open=9.9,
                high=10.5,
                low=9.8,
                close=10.0,
                volume=100.0,
            )
            for index in range(66)
        ]
        bars[-1] = Bar(date="d65", open=10.1, high=11.0, low=10.0, close=11.0, volume=200.0)
        return bars

    @staticmethod
    def _detect_descending_channel(bars: list[Bar]) -> tuple[bool, float | None, str | None, list[str]]:
        fixed_swings = [(5, "h0", 12.4), (25, "h1", 11.8), (45, "h2", 11.2)]
        with patch.object(agent, "indexed_swing_highs", return_value=fixed_swings):
            return agent.detect_descending_channel_breakout(
                bars,
                [bar.close for bar in bars],
                [bar.volume for bar in bars],
            )

    def test_descending_channel_stable_signal_weights_do_not_reverse_strong_candidate(self) -> None:
        base_signals = ["涨停突破下降通道", "收盘站上EMA20"]
        strong = SimpleNamespace(setup="下降通道突破", signals=base_signals + ["放量涨停", "量比2.0x"])
        weak = SimpleNamespace(setup="下降通道突破", signals=base_signals + ["放量配合", "量比1.3x"])

        self.assertGreaterEqual(agent.price_action_rank_score(strong), agent.price_action_rank_score(weak))

    def test_descending_channel_rejects_bearish_limit_up_candle(self) -> None:
        bars = self._descending_channel_bars()
        bars[-1] = Bar(date="d65", open=11.2, high=11.2, low=10.9, close=11.0, volume=200.0)

        detected, _, _, _ = self._detect_descending_channel(bars)

        self.assertFalse(detected)

    def test_descending_channel_rejects_yesterday_breakout(self) -> None:
        bars = self._descending_channel_bars()
        bars[-2] = Bar(date="d64", open=9.9, high=11.0, low=9.8, close=10.0, volume=100.0)

        detected, _, _, _ = self._detect_descending_channel(bars)

        self.assertFalse(detected)

    def test_descending_channel_rejects_earlier_intermediate_breakout(self) -> None:
        bars = self._descending_channel_bars()
        # window-local index 50 maps to bars[55]; yesterday remains below its projected top.
        bars[55] = Bar(date="d55", open=9.9, high=11.2, low=9.8, close=10.0, volume=100.0)

        detected, _, _, _ = self._detect_descending_channel(bars)

        self.assertFalse(detected)

    def test_descending_channel_accepts_reasonable_slope_first_breakout(self) -> None:
        bars = self._descending_channel_bars()

        detected, channel_top, broken_date, factors = self._detect_descending_channel(bars)

        self.assertTrue(detected)
        self.assertAlmostEqual(channel_top or 0.0, 10.75)
        self.assertEqual(broken_date, "d65")
        self.assertIn("涨停突破下降通道", factors)

    def test_higher_low_helper_is_available_to_daily_agent(self) -> None:
        bars = [
            Bar(
                date=(dt.date(2026, 1, 1) + dt.timedelta(days=index)).isoformat(),
                open=10 + index * 0.1,
                high=10.4 + index * 0.1,
                low=9.6 + index * 0.1,
                close=10.1 + index * 0.1,
                volume=1_000_000 + index,
            )
            for index in range(80)
        ]

        self.assertIsInstance(agent.higher_low(bars), bool)

    def test_build_report_with_strategy2_candidate_uses_range_position(self) -> None:
        candidate = RangeBoundCandidate(
            code="600001",
            name="Strategy2",
            date="2026-07-01",
            close=10.0,
            pct_change=1.0,
            amount=100_000_000.0,
            range_low=9.5,
            range_high=12.0,
            range_days=20,
            range_position=0.2,
            range_width_pct=26.0,
            gain_30=12.0,
            gain_60=30.0,
            buy_sell_ratio_60=2.5,
            ma30=9.8,
            ma60=8.8,
            volume_ratio=0.8,
            bullish_confidence=80.0,
            confidence_factors=["接近区间下沿"],
            bearish_confidence=20.0,
            bearish_factors=[],
            lower_edge_signals=["看涨Pinbar"],
        )

        report = build_report(
            candidates=[],
            trend_candidates=[],
            range_bound_candidates=[candidate],
            t0_fund_candidates=[],
            watchlist_reviews=[],
            errors=[],
            universe_size=1,
            t0_fund_universe_size=0,
            watchlist_size=0,
            indices=[],
            min_amount=80_000_000,
            min_buy_sell_ratio=2.0,
            t0_fund_min_amount=50_000_000,
        )

        self.assertIn("策略二再吸筹观察：600001 Strategy2", report)
        self.assertIn("箱体位置 20%", report)

    def test_three_push_wedge_bull_flag_reports_al_brooks_factor(self) -> None:
        bars: list[Bar] = []
        for index in range(45):
            low = 120.0 + index * 0.2
            open_price = low + 4.0
            close = low + 5.0
            high = low + 7.0
            if index == 20:
                low, open_price, close, high = 90.0, 94.0, 95.0, 97.0
            elif index == 30:
                low, open_price, close, high = 80.0, 84.0, 85.0, 87.0
            elif index == 40:
                low, open_price, close, high = 74.0, 78.0, 79.0, 81.0
            elif index == 44:
                low, open_price, close, high = 76.0, 81.0, 82.0, 83.0
            bars.append(
                Bar(
                    date=(dt.date(2026, 1, 1) + dt.timedelta(days=index)).isoformat(),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1_000_000 + index,
                )
            )

        wedge, factors = agent.three_push_wedge(bars, [bar.close for bar in bars])

        self.assertTrue(wedge)
        self.assertTrue(any("三推楔形牛旗" in factor for factor in factors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
