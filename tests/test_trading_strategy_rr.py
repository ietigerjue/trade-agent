"""Unit tests for the 2026-06-04 裸 K risk/reward rules added to trading_strategy.py.

Covers the five scenarios required by the Handoff:
1. fixed 5% stop math is correct
2. reward / reward-to-risk is correct when next pressure is above current price
3. candidates with pressure <= current price are filtered out
4. missing support / resistance inputs do not crash
5. Markdown and Lark formatters both contain RR / stop / pressure fields
"""

from __future__ import annotations

import os
import sys
import unittest

# 让测试可独立运行：把项目根目录加入 sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from trading_strategy import (  # noqa: E402
    Candle,
    FixedStopRR,
    compute_fixed_stop_rr,
    format_rr_lark_line,
    format_rr_markdown_row,
    identify_nearby_support,
    identify_next_pressure,
    rank_best_long_candidates,
)


def _bar(low: float, high: float, close: float, open_: float | None = None) -> Candle:
    """Build a single Candle with explicit low/high (open defaults to close)."""
    if open_ is None:
        open_ = close
    return Candle(open=open_, high=high, low=low, close=close, volume=1_000_000.0)


class IdentifyNearbySupportTests(unittest.TestCase):
    """Scenario 4a: identify_nearby_support must not crash on missing inputs."""

    def test_returns_none_on_empty_candles(self):
        self.assertIsNone(identify_nearby_support([], current_price=10.0))

    def test_returns_none_when_no_support_within_tolerance(self):
        # All candles are above the current price, so no support exists.
        candles = [
            _bar(11.0, 13.0, 12.0),
            _bar(12.5, 14.5, 13.5),
            _bar(13.5, 15.0, 14.5),
        ]
        self.assertIsNone(identify_nearby_support(candles, current_price=10.0))

    def test_finds_nearest_support_below_price(self):
        # Current price 10, last 4 candles dip to 9.7 / 9.6 → nearest is 9.7
        # (lows are kept close to closes so they fall within the 5% floor).
        candles = [
            _bar(10.4, 10.6, 10.5),
            _bar(9.7, 9.9, 9.8),
            _bar(9.6, 9.8, 9.7),
            _bar(9.5, 9.7, 9.6),
            _bar(9.55, 10.1, 10.0),
        ]
        support = identify_nearby_support(candles, current_price=10.0, lookback=5, max_distance_pct=0.05)
        self.assertIsNotNone(support)
        self.assertAlmostEqual(support, 9.7, places=2)

    def test_respects_max_distance_pct(self):
        # 9.0 is below the 5% tolerance from 10.0 → should be filtered out; 9.7 wins.
        candles = [
            _bar(10.4, 10.6, 10.5),
            _bar(9.0, 9.2, 9.1),  # too far below 10.0
            _bar(9.6, 9.85, 9.8),
            _bar(9.7, 10.1, 10.0),  # closest support to 10.0 within tolerance
        ]
        support = identify_nearby_support(candles, current_price=10.0, lookback=5, max_distance_pct=0.05)
        self.assertEqual(support, 9.7)


class IdentifyNextPressureTests(unittest.TestCase):
    """Scenario 4b: identify_next_pressure must not crash on missing inputs."""

    def test_returns_none_on_empty_candles(self):
        self.assertIsNone(identify_next_pressure([], current_price=10.0))

    def test_returns_none_when_no_pressure_above_min_pct(self):
        # All highs are within 2.5% of 10.0 → no qualifying pressure.
        candles = [
            _bar(9.5, 10.05, 9.95),
            _bar(9.6, 10.10, 9.98),
            _bar(9.55, 10.05, 9.90),
        ]
        self.assertIsNone(identify_next_pressure(candles, current_price=10.0))

    def test_finds_nearest_pressure_above_price(self):
        # highs reach 11.0 / 12.0 → nearest pressure is 11.0
        # (highs below 10.25 ceiling are filtered out).
        candles = [
            _bar(9.5, 10.1, 10.0),
            _bar(9.8, 10.2, 10.5),  # high=10.2 < ceiling 10.25
            _bar(10.5, 11.2, 11.0),
            _bar(11.5, 12.2, 12.0),
            _bar(10.5, 11.0, 10.8),
        ]
        pressure = identify_next_pressure(candles, current_price=10.0)
        self.assertIsNotNone(pressure)
        self.assertAlmostEqual(pressure, 11.0, places=2)


class ComputeFixedStopRRTests(unittest.TestCase):
    """Scenarios 1, 2, 3, 4c: fixed 5% stop + RR math + filtering."""

    def test_fixed_5pct_stop_is_correct(self):
        """Scenario 1: stop = current * 0.95 always."""
        rr = compute_fixed_stop_rr(
            current_price=10.0,
            nearby_support=9.5,
            next_pressure=12.0,
            stop_pct=0.05,
        )
        self.assertAlmostEqual(rr.stop_price, 9.5)
        self.assertAlmostEqual(rr.risk, 0.5)

    def test_rr_correct_when_pressure_above_price(self):
        """Scenario 2: risk=0.5, reward=2.0 → RR = 4.0."""
        rr = compute_fixed_stop_rr(
            current_price=10.0,
            nearby_support=9.5,
            next_pressure=12.0,
            stop_pct=0.05,
        )
        self.assertTrue(rr.is_valid_long_candidate)
        self.assertAlmostEqual(rr.reward, 2.0)
        self.assertAlmostEqual(rr.reward_to_risk, 4.0)

    def test_pressure_below_current_price_is_filtered(self):
        """Scenario 3: next_pressure <= current_price → invalid."""
        rr = compute_fixed_stop_rr(
            current_price=10.0,
            nearby_support=9.5,
            next_pressure=9.8,  # below current
            stop_pct=0.05,
        )
        self.assertFalse(rr.is_valid_long_candidate)
        self.assertEqual(rr.invalid_reason, "reward_non_positive")
        self.assertEqual(rr.reward_to_risk, 0.0)

    def test_missing_pressure_is_filtered_without_crashing(self):
        """Scenario 4: None pressure → invalid + invalid_reason set."""
        rr = compute_fixed_stop_rr(
            current_price=10.0,
            nearby_support=9.5,
            next_pressure=None,
            stop_pct=0.05,
        )
        self.assertFalse(rr.is_valid_long_candidate)
        self.assertEqual(rr.invalid_reason, "missing_next_pressure")
        self.assertEqual(rr.reward_to_risk, 0.0)

    def test_missing_support_does_not_crash(self):
        """Scenario 4: None support → still computes RR (support only informational)."""
        rr = compute_fixed_stop_rr(
            current_price=10.0,
            nearby_support=None,
            next_pressure=12.0,
            stop_pct=0.05,
        )
        self.assertTrue(rr.is_valid_long_candidate)
        self.assertAlmostEqual(rr.reward_to_risk, 4.0)
        self.assertIsNone(rr.nearby_support)

    def test_invalid_inputs_do_not_crash(self):
        """Scenario 4: garbage inputs return invalid FixedStopRR."""
        cases = [
            (-1.0, 9.5, 12.0),  # negative current price
            (0.0, 9.5, 12.0),   # zero current price
            (10.0, 9.5, 12.0),  # default stop_pct OK
        ]
        for current_price, support, pressure in cases:
            rr = compute_fixed_stop_rr(current_price, support, pressure)
            self.assertIsInstance(rr, FixedStopRR)
            if current_price <= 0:
                self.assertFalse(rr.is_valid_long_candidate)


class FormatterTests(unittest.TestCase):
    """Scenario 5: Markdown + Lark formatters both expose RR / stop / pressure."""

    def test_markdown_formatter_contains_required_fields(self):
        rr = compute_fixed_stop_rr(10.0, 9.5, 12.0, stop_pct=0.05)
        md = format_rr_markdown_row(rr)
        self.assertIn("5%止损", md)
        self.assertIn("9.50", md)  # stop_price
        self.assertIn("下一压力", md)
        self.assertIn("12.00", md)  # next_pressure
        self.assertIn("RR", md)
        self.assertIn("4.00", md)  # reward_to_risk
        self.assertIn("支撑", md)

    def test_lark_formatter_contains_required_fields(self):
        rr = compute_fixed_stop_rr(10.0, 9.5, 12.0, stop_pct=0.05)
        lark = format_rr_lark_line(rr)
        self.assertIn("5%止损", lark)
        self.assertIn("下一压力", lark)
        self.assertIn("RR", lark)
        self.assertIn("=" + "9.50", lark)
        self.assertIn("=" + "12.00", lark)
        self.assertIn("=" + "4.00", lark)

    def test_invalid_rr_markdown_formatter_does_not_crash(self):
        rr = compute_fixed_stop_rr(10.0, 9.5, None, stop_pct=0.05)
        md = format_rr_markdown_row(rr)
        self.assertIn("n/a", md)
        self.assertIn("missing_next_pressure", md)

    def test_invalid_rr_lark_formatter_does_not_crash(self):
        rr = compute_fixed_stop_rr(10.0, 9.5, None, stop_pct=0.05)
        lark = format_rr_lark_line(rr)
        self.assertIn("RR无效", lark)
        self.assertIn("missing_next_pressure", lark)


class RankBestLongCandidatesTests(unittest.TestCase):
    """Sort by RR desc → setup quality → final_score desc."""

    def test_filters_invalid_candidates_and_sorts_by_rr(self):
        candidates = [
            {"setup": "突破后回踩", "reward_to_risk": 4.0, "final_score": 50.0, "bullish_confidence": 80.0},
            {"setup": "突破后回踩", "reward_to_risk": 2.0, "final_score": 90.0, "bullish_confidence": 95.0},
            {"setup": "二波回踩EMA20", "reward_to_risk": 3.0, "final_score": 70.0, "bullish_confidence": 85.0},
            {"setup": "突破后回踩", "reward_to_risk": None, "final_score": 99.0, "bullish_confidence": 99.0},  # invalid
            {"setup": "突破后回踩", "reward_to_risk": 9.0, "final_score": 99.0, "bullish_confidence": 99.0, "is_valid_long_candidate": False},
            {"setup": "观察", "reward_to_risk": 5.0, "final_score": 99.0, "bullish_confidence": 99.0},  # setup not actionable
            {"setup": "二波回踩EMA20", "reward_to_risk": 3.0, "final_score": 80.0, "bullish_confidence": 80.0},
        ]
        ranked = rank_best_long_candidates(candidates)
        self.assertEqual(len(ranked), 4)  # 2 invalid filtered out
        # Primary sort: RR desc → 4.0 first, 3.0 (90-> better setup then 80), 2.0 last
        self.assertAlmostEqual(ranked[0]["reward_to_risk"], 4.0)
        # Second pair ties on RR=3.0, so setup quality breaks the tie (二波回踩EMA20=92 > 突破后回踩=88)
        self.assertEqual(ranked[1]["setup"], "二波回踩EMA20")
        self.assertEqual(ranked[2]["setup"], "二波回踩EMA20")
        self.assertAlmostEqual(ranked[3]["reward_to_risk"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
