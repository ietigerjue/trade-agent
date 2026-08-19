"""Generate a sample Markdown report using mock data to inspect 2026-06-04 RR section output."""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from a_share_daily_agent import (  # noqa: E402
    AShareCandidate,
    build_report,
)
from tests.test_build_report_rr import _candidate  # type: ignore  # noqa: E402


def main() -> int:
    candidates = [
        _candidate("600001", "Alpha", close=10.0, support=9.5, target=12.0, reward_risk=2.0, final_score=72.0),
        _candidate("600002", "Beta", close=20.0, support=19.0, target=24.0, reward_risk=2.0, final_score=80.0),
        _candidate("600003", "Gamma", close=8.0, support=7.6, target=9.6, reward_risk=2.0, final_score=68.0),
        _candidate("600004", "Delta", close=15.0, support=14.0, target=18.5, reward_risk=2.3, final_score=85.0,
                   setup="二波回踩EMA20", bullish_confidence=92.0),
        # invalid: target below current price → filtered out
        _candidate("600999", "Invalid", close=15.0, support=14.0, target=14.5, reward_risk=2.0, final_score=99.0),
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
    # Print only the new fixed-RR section + the quick-conclusion block for inspection
    lines = report.splitlines()
    out_idx = [i for i, line in enumerate(lines) if "固定5%止损" in line or "2026-06-04 裸 K risk/reward" in line or "综合最值得观察" in line]
    print("=" * 70)
    print("=== 快速结论 + 固定5%止损 RR 段 (sample) ===")
    print("=" * 70)
    print("\n".join(lines))
    print("=" * 70)
    print(f"\n=== 关键行索引（fixed-RR 相关）===")
    for i in out_idx:
        print(f"  line {i}: {lines[i][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())