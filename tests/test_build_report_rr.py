"""Dry-run test for build_report with the 2026-06-04 裸 K RR rules.

Constructs mock AShareCandidate objects and verifies that build_report:
- Includes the new "## 最优做多候选（固定5%止损 · 2026-06-04 规则）" section
- Uses the fixed RR-ranked top candidate in "快速结论"
- Drops candidates whose fixed 5% RR is invalid (pressure <= current price)
  from all surfaced long-candidate report areas

This does NOT touch the network — only the pure build_report function.
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import a_share_daily_agent as agent_mod  # noqa: E402
from a_share_daily_agent import (  # noqa: E402
    AShareCandidate,
    build_report,
)


def _candidate(
    code: str,
    name: str,
    close: float,
    support: float,
    target: float,
    setup: str = "突破后回踩",
    reward_risk: float = 2.0,
    bullish_confidence: float = 85.0,
    final_score: float = 70.0,
) -> AShareCandidate:
    """Build a minimal AShareCandidate with the fields build_report / score_price_action use."""
    return AShareCandidate(
        code=code,
        name=name,
        date="2026-06-13",
        close=close,
        pct_change=1.5,
        amount=200_000_000.0,
        setup=setup,
        signals=["回踩不破"],
        support=support,
        support_date="2026-06-05",
        stop=close * 0.97,  # existing structural stop (different from fixed 5%)
        target=target,
        target_date="2026-05-15",
        reward_risk=reward_risk,
        reward_risk_confidence=0.7,
        ma5=close * 0.99,
        ma10=close * 0.98,
        ma20=close * 0.97,
        ma30=close * 0.95,
        ma60=close * 0.90,
        volume_ratio=1.1,
        gain_30=8.0,
        velocity_30=0.27,
        gain_60=12.0,
        buy_sell_ratio_60=2.2,
        bullish_confidence=bullish_confidence,
        confidence_factors=["强趋势", "板块共振"],
        bearish_confidence=15.0,
        bearish_factors=[],
        false_breaks=0,
        final_score=final_score,
    )


class BuildReportFixedRRTests(unittest.TestCase):
    def test_build_report_includes_fixed_rr_section(self):
        candidates = [
            _candidate("600001", "Alpha", close=10.0, support=9.5, target=12.0, reward_risk=2.0),
            _candidate("600002", "Beta", close=20.0, support=19.0, target=24.0, reward_risk=2.0),
            _candidate("600003", "Gamma", close=8.0, support=7.6, target=9.6, reward_risk=2.0),
        ]
        report = build_report(
            candidates=candidates,
            trend_candidates=[],
            range_bound_candidates=[],
            t0_fund_candidates=[],
            watchlist_reviews=[],
            errors=[],
            universe_size=len(candidates),
            t0_fund_universe_size=0,
            watchlist_size=0,
            indices=[],
            min_amount=80_000_000,
            min_buy_sell_ratio=2.0,
            t0_fund_min_amount=50_000_000,
        )
        self.assertIn("## 最优做多候选（固定5%止损 · 2026-06-04 规则）", report)
        # Quick conclusion should reference the fixed-RR top
        self.assertIn("综合最值得观察（按固定5%止损 RR · 2026-06-04 规则）", report)
        # Method section should describe the rule
        self.assertIn("2026-06-04 裸 K risk/reward 规则", report)
        # All three candidates have valid fixed 5% RR (target > close)
        self.assertIn("600001 Alpha", report)
        self.assertIn("600002 Beta", report)
        self.assertIn("600003 Gamma", report)

    def test_build_report_filters_invalid_rr_candidates(self):
        # 600999 has target < close → fixed RR is invalid → must not appear in surfaced best
        candidates = [
            _candidate("600001", "Alpha", close=10.0, support=9.5, target=12.0, reward_risk=2.0),
            _candidate("600002", "Beta", close=20.0, support=19.0, target=24.0, reward_risk=2.0),
            _candidate("600999", "Invalid", close=15.0, support=14.0, target=14.5, reward_risk=2.0),  # target < close
        ]
        report = build_report(
            candidates=candidates,
            trend_candidates=[],
            range_bound_candidates=[],
            t0_fund_candidates=[],
            watchlist_reviews=[],
            errors=[],
            universe_size=len(candidates),
            t0_fund_universe_size=0,
            watchlist_size=0,
            indices=[],
            min_amount=80_000_000,
            min_buy_sell_ratio=2.0,
            t0_fund_min_amount=50_000_000,
            fixed_rr_top_n=5,
        )
        # The fixed-RR section should mention "上榜 N / 全候选 M" with M=3 but N<=2
        self.assertIn("上榜 2 / 全候选 3", report)
        # 600999 must NOT appear anywhere in the surfaced daily report areas.
        fixed_section = report.split("## 最优做多候选（固定5%止损")[1].split("## 趋势良好观察池")[0]
        self.assertNotIn("600999", fixed_section)
        self.assertNotIn("600999", report)

    def test_build_report_with_no_candidates(self):
        report = build_report(
            candidates=[],
            trend_candidates=[],
            range_bound_candidates=[],
            t0_fund_candidates=[],
            watchlist_reviews=[],
            errors=[],
            universe_size=0,
            t0_fund_universe_size=0,
            watchlist_size=0,
            indices=[],
            min_amount=80_000_000,
            min_buy_sell_ratio=2.0,
            t0_fund_min_amount=50_000_000,
        )
        # Section header should still appear
        self.assertIn("## 最优做多候选（固定5%止损 · 2026-06-04 规则）", report)
        # Empty note should mention "无满足固定5%止损 RR 规则"
        fixed_section = report.split("## 最优做多候选（固定5%止损")[1].split("## 趋势良好观察池")[0]
        self.assertIn("无满足固定5%止损 RR 规则的最优做多候选", fixed_section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
