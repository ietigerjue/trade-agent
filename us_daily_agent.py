#!/usr/bin/env python3
"""Daily US stock scanner — applies A-share price-action strategies to US equities.

Imports strategy scoring functions from a_share_daily_agent and applies them
to US stocks via the Yahoo Finance data adapter. US-specific enrichment omits
sector/community/news fields (no equivalent data sources for US equities).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trading_strategy import Bar, FixedStopRR, compute_fixed_stop_rr, format_rr_markdown_row, rank_best_long_candidates

from us_market_data import (
    fetch_us_daily_bars,
    build_us_quote,
    fetch_us_indices,
    load_us_universe,
)

# ── Import A-share strategy functions ───────────────────────────────────
from a_share_daily_agent import (
    AShareCandidate,
    RangeBoundCandidate,
    TrendCandidate,
    append_candidate_table,
    candidate_row,
    compute_fixed_rr_for_candidates,
    fixed_rr_best_long_section,
    price_action_rank_score,
    range_bound_row,
    rank_candidates_by_fixed_rr,
    score_price_action,
    score_range_bound,
    score_trend_candidate,
    trend_row,
)

DEFAULT_REPORT_DIR = Path(
    "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/us/daily"
)

BROOKS_BULLISH_KEYWORDS = (
    "强趋势背景",
    "突破后站稳",
    "二次回踩",
    "三推楔形牛旗",
    "回踩不破",
    "裸Ksetup",
    "EMA20趋势支撑",
    "EMA20支撑确认",
)

BROOKS_BEARISH_KEYWORDS = (
    "重叠K",
    "交易区间上沿",
    "弱突破",
    "跌回压力位",
    "假突破",
    "多重顶",
    "M字顶",
)


# ── US-specific enrichment ──────────────────────────────────────────────

def enrich_us_candidate(candidate: AShareCandidate) -> AShareCandidate:
    """US enrichment: omit sector/community/news, compute price-action-only final_score."""
    price_score = price_action_rank_score(candidate)
    trend_score = (
        candidate.gain_60 * 0.06
        + candidate.velocity_30 * 0.8
        + min(candidate.buy_sell_ratio_60, 5) * 0.8
    )
    final_score = (
        price_score
        + candidate.reward_risk * 6
        + candidate.volume_ratio * 2
        + trend_score
        + (candidate.bullish_confidence - 50) * 0.15
        - max(candidate.bearish_confidence - 35, 0) * 0.25
        - candidate.false_breaks * 2.0
    )
    return replace(
        candidate,
        industry=None,
        concepts=None,
        community=None,
        latest_note=None,
        final_score=final_score,
    )


def enrich_us_trend_candidate(candidate: TrendCandidate) -> TrendCandidate:
    """US trend enrichment: set sector/community fields to None."""
    return replace(
        candidate,
        industry=None,
        concepts=None,
        community=None,
        latest_note=None,
    )


def enrich_us_range_bound_candidate(candidate: RangeBoundCandidate) -> RangeBoundCandidate:
    """US range-bound enrichment: set sector/community fields to None."""
    return replace(
        candidate,
        industry=None,
        concepts=None,
        community=None,
        latest_note=None,
    )


def _brooks_factor_matches(factors: list[str], keywords: tuple[str, ...]) -> list[str]:
    """Return factors that match the Al Brooks price-action keyword set."""
    return [
        factor
        for factor in factors
        if any(keyword in factor for keyword in keywords)
    ]


def build_brooks_price_action_section(
    candidates: list[AShareCandidate],
    range_bound_candidates: list[RangeBoundCandidate],
) -> str:
    """Build a focused Al Brooks price-action section for the US report."""
    lines: list[str] = [
        "## Al Brooks价格行为观察",
        "",
        "只把已经进入美股做多候选/策略二观察池的股票再按价格行为拆解；这是置信度解释层，不是新的硬过滤器。",
        "",
        "| 股票 | 形态 | 看涨确认 | 看跌风险 | 处理口径 |",
        "|---|---|---|---|---|",
    ]

    rows: list[str] = []
    for candidate in candidates:
        bullish = _brooks_factor_matches(candidate.confidence_factors, BROOKS_BULLISH_KEYWORDS)
        bearish = _brooks_factor_matches(candidate.bearish_factors, BROOKS_BEARISH_KEYWORDS)
        if not bullish and not bearish:
            continue
        decision = "优先观察回踩承接" if candidate.bullish_confidence >= 75 and candidate.bearish_confidence < 45 else "等待下一根K线确认"
        rows.append(
            f"| {candidate.code} {candidate.name} | {candidate.setup} | "
            f"{'; '.join(bullish) if bullish else '暂无明确Brooks看涨因子'} | "
            f"{'; '.join(bearish) if bearish else '未见主要Brooks风险'} | {decision} |"
        )

    for candidate in range_bound_candidates:
        bullish = _brooks_factor_matches(
            candidate.confidence_factors + candidate.lower_edge_signals,
            BROOKS_BULLISH_KEYWORDS + ("看涨Pinbar", "更高低点"),
        )
        bearish = _brooks_factor_matches(candidate.bearish_factors, BROOKS_BEARISH_KEYWORDS)
        if not bullish and not bearish:
            continue
        decision = "只看区间下沿承接" if candidate.bullish_confidence >= 72 else "继续等右侧确认"
        rows.append(
            f"| {candidate.code} {candidate.name} | 策略二下沿观察 | "
            f"{'; '.join(bullish) if bullish else '暂无明确Brooks看涨因子'} | "
            f"{'; '.join(bearish) if bearish else '未见主要Brooks风险'} | {decision} |"
        )

    if rows:
        lines.extend(rows)
    else:
        lines.append("| - | - | - | - | 当前美股候选里没有明显的 Brooks 强确认或主要风险因子。 |")

    return "\n".join(lines)


# ── Per-symbol processing ───────────────────────────────────────────────

def process_us_symbol(
    symbol: str,
    min_buy_sell_ratio: float,
) -> tuple[AShareCandidate | None, TrendCandidate | None, RangeBoundCandidate | None, str | None]:
    """Process one US symbol through all three A-share strategies.

    Mirrors a_share_daily_agent.process_quote() but uses us_market_data.
    """
    try:
        bars = fetch_us_daily_bars(symbol)
        quote = build_us_quote(symbol, bars)
        name = quote["name"]
        price_action = score_price_action(symbol, name, quote, bars, min_buy_sell_ratio)
        trend = score_trend_candidate(symbol, name, quote, bars, min_buy_sell_ratio)
        wyckoff = score_range_bound(symbol, name, quote, bars, min_buy_sell_ratio)
        return price_action, trend, wyckoff, None
    except Exception as exc:
        return None, None, None, f"{symbol}: {exc}"


# ── Scanning ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class USScanResult:
    candidates: list[AShareCandidate]
    trend_candidates: list[TrendCandidate]
    range_bound_candidates: list[RangeBoundCandidate]
    errors: list[str]
    universe_size: int
    bars_map: dict[str, list[Bar]]


def scan_us_stocks(
    top: int = 8,
    workers: int = 12,
    min_buy_sell_ratio: float = 1.8,
    universe_file: str | None = None,
) -> USScanResult:
    """Parallel scan of US stock universe using A-share strategy functions."""
    tickers = load_us_universe(universe_file)
    candidates: list[AShareCandidate] = []
    trend_candidates: list[TrendCandidate] = []
    range_bound_candidates: list[RangeBoundCandidate] = []
    errors: list[str] = []
    bars_map: dict[str, list[Bar]] = {}

    with ThreadPoolExecutor(max_workers=min(workers, len(tickers))) as pool:
        futures = {
            pool.submit(process_us_symbol, sym, min_buy_sell_ratio): sym
            for sym in tickers
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                pa, trend, wyckoff, err = future.result()
            except Exception as exc:
                errors.append(f"{sym}: {exc}")
                continue
            if err:
                errors.append(err)
                continue
            if pa is not None:
                candidates.append(pa)
            if trend is not None:
                trend_candidates.append(trend)
            if wyckoff is not None:
                range_bound_candidates.append(wyckoff)

    # Sort and deduplicate
    candidates.sort(key=lambda c: c.final_score, reverse=True)
    trend_candidates.sort(key=lambda c: c.final_score, reverse=True)
    range_bound_candidates.sort(key=lambda c: c.final_score, reverse=True)

    seen = set()
    deduped: list[AShareCandidate] = []
    for c in candidates:
        key = c.code
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    candidates = deduped[:top * 2]  # keep extra for enrichment then filter

    # Enrich candidates
    enriched: list[AShareCandidate] = []
    for c in candidates:
        enriched.append(enrich_us_candidate(c))
    candidates = enriched

    enriched_trend: list[TrendCandidate] = []
    for c in trend_candidates[:top * 2]:
        enriched_trend.append(enrich_us_trend_candidate(c))

    enriched_rb: list[RangeBoundCandidate] = []
    for c in range_bound_candidates[:top]:
        enriched_rb.append(enrich_us_range_bound_candidate(c))

    # Fetch bars for FixedStopRR computation
    for c in candidates:
        try:
            bars_map[c.code] = fetch_us_daily_bars(c.code)
        except Exception:
            pass

    return USScanResult(
        candidates=candidates[:top],
        trend_candidates=enriched_trend[:top],
        range_bound_candidates=enriched_rb[:top],
        errors=errors,
        universe_size=len(tickers),
        bars_map=bars_map,
    )


# ── Report building ─────────────────────────────────────────────────────

def build_us_section(
    result: USScanResult,
    us_date: str,
    fixed_rr_top_n: int = 5,
) -> str:
    """Build the US stock section of the combined report.

    Returns markdown string with all US sections.
    """
    lines: list[str] = []
    lines.append(f"# 美股部分 ({us_date} ET)")
    lines.append("")

    # ── US market indices ───────────────────────────────────────────
    indices = fetch_us_indices()
    if indices:
        lines.append("## 美股指数环境")
        lines.append("")
        lines.append("| 指数 | 最新价 | 涨跌幅 |")
        lines.append("|---|---|---|")
        for idx in indices:
            change = f"{idx['change_pct']:+.2f}%" if idx['change_pct'] else "n/a"
            lines.append(f"| {idx['name']} | {idx['price']} | {change} |")
        lines.append("")

    # ── Fixed RR computation ────────────────────────────────────────
    fixed_rr_map = compute_fixed_rr_for_candidates(result.candidates)
    ranked_by_rr = rank_candidates_by_fixed_rr(result.candidates, fixed_rr_map)

    # ── Strategy candidates ─────────────────────────────────────────
    strategy_a: list[AShareCandidate] = []
    strategy_b: list[AShareCandidate] = []
    strategy_other: list[AShareCandidate] = []
    for c in result.candidates:
        s = (c.setup or "").lower()
        if "回踩" in s or "retest" in s:
            strategy_a.append(c)
        elif "二波" in s or "second" in s or "ema20" in s:
            strategy_b.append(c)
        else:
            strategy_other.append(c)

    # ── Long candidates ─────────────────────────────────────────────
    lines.append("## 做多候选")
    lines.append("")

    if strategy_a:
        lines.append("### 做多候选A：突破后回踩前压力位")
        lines.append("")
        for c in strategy_a:
            lines.append(candidate_row(c, fixed_rr_map.get(c.code)))
        lines.append("")

    if strategy_b:
        lines.append("### 做多候选B：强趋势二波回踩EMA20")
        lines.append("")
        for c in strategy_b:
            lines.append(candidate_row(c, fixed_rr_map.get(c.code)))
        lines.append("")

    if strategy_other:
        lines.append("### 做多候选补充：其他形态")
        lines.append("")
        for c in strategy_other:
            lines.append(candidate_row(c, fixed_rr_map.get(c.code)))
        lines.append("")

    if not strategy_a and not strategy_b and not strategy_other:
        lines.append("*今日无符合条件的做多候选*")
        lines.append("")

    # ── Al Brooks price-action layer ────────────────────────────────
    lines.append(
        build_brooks_price_action_section(
            result.candidates,
            result.range_bound_candidates,
        )
    )
    lines.append("")

    # ── Fixed RR best long ──────────────────────────────────────────
    lines.extend(
        fixed_rr_best_long_section(
            ranked_by_rr, fixed_rr_map, top_n=fixed_rr_top_n
        )
    )
    lines.append("")

    # ── Trend pool ──────────────────────────────────────────────────
    lines.append("## 趋势良好观察池")
    lines.append("")
    if result.trend_candidates:
        for c in result.trend_candidates:
            lines.append(trend_row(c))
    else:
        lines.append("*今日无趋势候选*")
    lines.append("")

    # ── Strategy 2: Range-bound ─────────────────────────────────────
    lines.append("## 策略二：震荡区间选股观察")
    lines.append("")
    if result.range_bound_candidates:
        for c in result.range_bound_candidates:
            lines.append(range_bound_row(c))
    else:
        lines.append("*今日无震荡区间候选*")
    lines.append("")

    # ── US data notes ───────────────────────────────────────────────
    lines.append("## 美股数据说明")
    lines.append("")
    lines.append("- 美股OHLCV数据来源：Yahoo Finance公开接口，可能有15分钟延迟")
    lines.append("- 美股暂不支持行业/概念板块强度分析（无等效CFI数据源）")
    lines.append("- 美股暂不支持社区讨论热度分析（无等效东方财富股吧数据源）")
    lines.append("- 上述缺失字段在表格中标注为 `n/a`")
    lines.append("- Al Brooks价格行为层来自同一套裸K评分：强趋势、突破跟随、二次回踩不破、三推楔形牛旗提高看涨置信度；重叠K、区间上沿首次突破、弱突破和跌回突破位提高风险提示。")
    lines.append(f"- 扫描标的池：{result.universe_size} 只美股（高流动性大盘/中盘股）")
    lines.append("")

    if result.errors:
        lines.append("## 数据备注")
        lines.append("")
        for err in result.errors[:20]:
            lines.append(f"- {err}")
        if len(result.errors) > 20:
            lines.append(f"- ... 共 {len(result.errors)} 条错误")
        lines.append("")

    return "\n".join(lines)


# ── Standalone entry point ──────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="US daily stock scanner")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--min-buy-sell-ratio", type=float, default=1.8)
    parser.add_argument("--universe-file", default=None)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--output", "-o", default=None, help="Write report to this file instead of default location")
    args = parser.parse_args()

    result = scan_us_stocks(
        top=args.top,
        workers=args.workers,
        min_buy_sell_ratio=args.min_buy_sell_ratio,
        universe_file=args.universe_file,
    )

    today = dt.date.today()
    # US section date is yesterday (US market close = 04:00 HKT next day)
    us_date = (today - dt.timedelta(days=1)).strftime("%Y-%m-%d")

    section = build_us_section(result, us_date, fixed_rr_top_n=min(args.top, 5))

    full_report = section
    # Prepend a minimal standalone header
    full_report = (
        f"# 每日美股裸K做多观察报告\n\n"
        f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} HKT\n\n"
        f"> 美股收盘于美东16:00=北京时间次日04:00。本报告分析的是美东 {us_date} 的收盘数据。\n\n"
        + section
    )

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output or (report_dir / f"us_daily_{us_date}.md")
    Path(report_path).write_text(full_report, encoding="utf-8")
    latest = report_dir / "us_latest.md"
    latest.write_text(full_report, encoding="utf-8")

    print(f"US daily report written to {report_path}")
    print(f"Candidates: {len(result.candidates)} price-action, "
          f"{len(result.trend_candidates)} trend, "
          f"{len(result.range_bound_candidates)} range-bound")
    print(f"Errors: {len(result.errors)}")

    # Individual stock fetch errors are normal (API rate limits, network blips).
    # Only fail the run if we got zero candidates across all categories, which
    # indicates a systemic failure (e.g. API down, auth error).
    total_candidates = (
        len(result.candidates)
        + len(result.trend_candidates)
        + len(result.range_bound_candidates)
    )
    if total_candidates == 0 and result.errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
