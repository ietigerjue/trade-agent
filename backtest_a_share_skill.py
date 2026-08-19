"""
Backtest the current A-share price-action skill on historical daily bars.

Two modes are supported:
- slice: one historical signal date, then measure N trading days later.
- rolling: many historical signal dates, select top-N candidates each date,
  then measure 5/10/20 day outcomes, path risk, stop hits, and factor groups.
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import a_share_daily_agent as agent


@dataclass(frozen=True)
class QuoteBars:
    quote: dict[str, Any]
    bars: list[agent.Bar]


@dataclass(frozen=True)
class CandidateAtDate:
    code: str
    name: str
    signal_index: int
    candidate: agent.AShareCandidate
    bars: list[agent.Bar]


@dataclass(frozen=True)
class BacktestTrade:
    code: str
    name: str
    signal_date: str
    entry_date: str
    hold_days: int
    exit_date: str
    entry_close: float
    exit_close: float
    return_pct: float
    mfe_pct: float
    mae_pct: float
    stop_hit: bool
    breakeven_stop_hit: bool
    target_hit: bool
    benchmark_return_pct: float | None
    bullish_confidence: float
    bearish_confidence: float
    setup: str
    signals: list[str]
    confidence_factors: list[str]
    reward_risk: float
    volume_ratio: float
    entry_rule: str
    exit_rule: str


def parse_date(value: str | None) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now().date()


def parse_hold_days(raw: str) -> list[int]:
    return sorted({int(item.strip()) for item in raw.split(",") if item.strip()})


def date_of(bar: agent.Bar) -> dt.date:
    return dt.date.fromisoformat(bar.date)


def find_signal_index(bars: list[agent.Bar], target_date: dt.date) -> int | None:
    signal_index: int | None = None
    for index, bar in enumerate(bars):
        if date_of(bar) <= target_date:
            signal_index = index
        else:
            break
    return signal_index


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def pct(value: float) -> float:
    return value * 100


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def win_rate(values: list[float]) -> float:
    return sum(1 for value in values if value > 0) / len(values) * 100 if values else 0.0


def fetch_quote_bars(quote: dict[str, Any]) -> tuple[QuoteBars | None, str | None]:
    code = str(quote.get("code") or "")
    name = str(quote.get("name") or code)
    try:
        return QuoteBars(quote=quote, bars=agent.fetch_daily_bars(code)), None
    except Exception as exc:
        return None, f"{code} {name}: {exc}"


def rank_candidate(item: CandidateAtDate) -> tuple[float, float, float, float]:
    return (
        item.candidate.bullish_confidence,
        agent.price_action_rank_score(item.candidate),
        item.candidate.reward_risk,
        -item.candidate.false_breaks,
    )


def benchmark_bars() -> list[agent.Bar]:
    try:
        rows = agent.fetch_json(
            agent.SINA_KLINE_URL,
            {"symbol": "sh000300", "scale": "240", "ma": "no", "datalen": "650"},
            retries=3,
            timeout=15,
        )
        bars = [
            agent.Bar(
                date=str(row["day"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
            )
            for row in rows[-620:]
        ]
        return bars
    except Exception:
        return []


def benchmark_return(
    bench_by_date: dict[str, agent.Bar],
    signal_date: str,
    exit_date: str,
) -> float | None:
    start = bench_by_date.get(signal_date)
    end = bench_by_date.get(exit_date)
    if not start or not end or start.close <= 0:
        return None
    return (end.close - start.close) / start.close * 100


def next_day_retest_entry(
    item: CandidateAtDate,
    *,
    buy_zone_pct: float = 0.02,
    breakdown_pct: float = 0.015,
) -> tuple[int, float, str] | None:
    entry_index = item.signal_index + 1
    if entry_index >= len(item.bars):
        return None

    next_bar = item.bars[entry_index]
    breakout_level = item.candidate.support
    buy_zone_upper = breakout_level * (1 + buy_zone_pct)
    breakdown_line = breakout_level * (1 - breakdown_pct)

    touched_buy_zone = next_bar.low <= buy_zone_upper
    held_breakout_line = next_bar.low >= breakdown_line and next_bar.close >= breakout_level
    if not touched_buy_zone or not held_breakout_line:
        return None

    if next_bar.open <= buy_zone_upper:
        entry_price = next_bar.open
        rule = "次日开盘位于突破价附近且不破"
    elif next_bar.low <= breakout_level <= next_bar.high:
        entry_price = breakout_level
        rule = "次日回踩突破价不破"
    else:
        entry_price = buy_zone_upper
        rule = "次日回踩突破价2%以内不破"

    return entry_index, entry_price, rule


def make_trade(
    item: CandidateAtDate,
    hold_days: int,
    bench_by_date: dict[str, agent.Bar],
    breakeven_trigger_pct: float | None = None,
) -> BacktestTrade | None:
    entry = next_day_retest_entry(item)
    if entry is None:
        return None
    entry_index, entry_price, entry_rule = entry
    exit_index = entry_index + hold_days
    if exit_index >= len(item.bars):
        return None
    candidate = item.candidate
    signal = item.bars[item.signal_index]
    entry_bar = item.bars[entry_index]
    scheduled_exit_bar = item.bars[exit_index]
    path = item.bars[entry_index : exit_index + 1]
    if not path or entry_price <= 0:
        return None

    max_high = max(bar.high for bar in path)
    min_low = min(bar.low for bar in path)
    stop_price = candidate.stop if 0 < candidate.stop < entry_price else entry_price * 0.95
    exit_bar = scheduled_exit_bar
    exit_price = scheduled_exit_bar.close
    exit_rule = f"{hold_days}日收盘"
    breakeven_armed = False
    breakeven_stop_hit = False
    for offset, bar in enumerate(path):
        if offset == 0:
            continue
        if breakeven_trigger_pct is not None and not breakeven_armed:
            trigger_price = entry_price * (1 + breakeven_trigger_pct / 100)
            if bar.high >= trigger_price:
                breakeven_armed = True
        if breakeven_armed and bar.low <= entry_price:
            exit_bar = bar
            exit_price = entry_price
            exit_rule = f"浮盈{breakeven_trigger_pct:.1f}%后保本止损"
            breakeven_stop_hit = True
            break

    return BacktestTrade(
        code=item.code,
        name=item.name,
        signal_date=signal.date,
        entry_date=entry_bar.date,
        hold_days=hold_days,
        exit_date=exit_bar.date,
        entry_close=entry_price,
        exit_close=exit_price,
        return_pct=(exit_price - entry_price) / entry_price * 100,
        mfe_pct=(max_high - entry_price) / entry_price * 100,
        mae_pct=(min_low - entry_price) / entry_price * 100,
        stop_hit=any(bar.low <= stop_price for bar in path),
        breakeven_stop_hit=breakeven_stop_hit,
        target_hit=any(bar.high >= candidate.target for bar in path),
        benchmark_return_pct=benchmark_return(bench_by_date, entry_bar.date, exit_bar.date),
        bullish_confidence=candidate.bullish_confidence,
        bearish_confidence=candidate.bearish_confidence,
        setup=candidate.setup,
        signals=candidate.signals,
        confidence_factors=candidate.confidence_factors,
        reward_risk=candidate.reward_risk,
        volume_ratio=candidate.volume_ratio,
        entry_rule=entry_rule,
        exit_rule=exit_rule,
    )


def sample_dates_from_reference(
    bars: list[agent.Bar],
    *,
    as_of: dt.date,
    lookback_days: int,
    sample_step: int,
    max_hold_days: int,
) -> list[dt.date]:
    start = as_of - dt.timedelta(days=lookback_days)
    eligible: list[dt.date] = []
    for index, bar in enumerate(bars):
        bar_date = date_of(bar)
        if start <= bar_date <= as_of and index + max_hold_days < len(bars):
            eligible.append(bar_date)
    if not eligible:
        return []
    return eligible[:: max(1, sample_step)]


def collect_candidates_for_dates(
    loaded: list[QuoteBars],
    sample_dates: list[dt.date],
    min_buy_sell_ratio: float,
) -> dict[str, list[CandidateAtDate]]:
    by_date: dict[str, list[CandidateAtDate]] = defaultdict(list)
    sample_set = {date.isoformat() for date in sample_dates}
    for item in loaded:
        code = str(item.quote.get("code") or "")
        name = str(item.quote.get("name") or code)
        date_to_index = {bar.date: index for index, bar in enumerate(item.bars)}
        for signal_date in sample_set:
            index = date_to_index.get(signal_date)
            if index is None or index < 130:
                continue
            history = item.bars[: index + 1]
            candidate = agent.score_price_action(code, name, item.quote, history, min_buy_sell_ratio)
            if candidate is None:
                continue
            by_date[signal_date].append(
                CandidateAtDate(
                    code=code,
                    name=name,
                    signal_index=index,
                    candidate=candidate,
                    bars=item.bars,
                )
            )
    return by_date


def factor_names(trade: BacktestTrade) -> list[str]:
    names: list[str] = []
    for factor in [trade.setup] + trade.signals + trade.confidence_factors:
        if factor.startswith("放量突破("):
            names.append("放量突破")
        else:
            names.append(factor)
    return names


def metric_rows(trades: list[BacktestTrade], hold_days_list: list[int]) -> list[str]:
    rows = [
        "| 持有 | 样本数 | 平均收益 | 中位数 | 胜率 | 平均超额沪深300 | 平均MFE | 平均MAE | 结构止损触发 | 保本止损触发 | 目标触达 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for hold_days in hold_days_list:
        subset = [trade for trade in trades if trade.hold_days == hold_days]
        returns = [trade.return_pct for trade in subset]
        excess = [
            trade.return_pct - trade.benchmark_return_pct
            for trade in subset
            if trade.benchmark_return_pct is not None
        ]
        stop_rate = sum(1 for trade in subset if trade.stop_hit) / len(subset) * 100 if subset else 0.0
        breakeven_rate = sum(1 for trade in subset if trade.breakeven_stop_hit) / len(subset) * 100 if subset else 0.0
        target_rate = sum(1 for trade in subset if trade.target_hit) / len(subset) * 100 if subset else 0.0
        rows.append(
            f"| {hold_days}日 | {len(subset)} | {fmt_pct(mean(returns))} | {fmt_pct(median(returns))} | "
            f"{win_rate(returns):.1f}% | {fmt_pct(mean(excess)) if excess else '-'} | "
            f"{fmt_pct(mean([trade.mfe_pct for trade in subset]))} | {fmt_pct(mean([trade.mae_pct for trade in subset]))} | "
            f"{stop_rate:.1f}% | {breakeven_rate:.1f}% | {target_rate:.1f}% |"
        )
    return rows


def factor_rows(trades: list[BacktestTrade], hold_days: int, min_count: int) -> list[str]:
    grouped: dict[str, list[BacktestTrade]] = defaultdict(list)
    for trade in trades:
        if trade.hold_days != hold_days:
            continue
        for factor in factor_names(trade):
            grouped[factor].append(trade)
    ranked = sorted(
        ((factor, items) for factor, items in grouped.items() if len(items) >= min_count),
        key=lambda pair: (mean([trade.return_pct for trade in pair[1]]), len(pair[1])),
        reverse=True,
    )
    rows = [
        f"| 因子/信号 | 样本数 | {hold_days}日平均收益 | 中位数 | 胜率 | 结构止损触发 | 保本止损触发 | 目标触达 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for factor, items in ranked[:25]:
        returns = [trade.return_pct for trade in items]
        stop_rate = sum(1 for trade in items if trade.stop_hit) / len(items) * 100
        breakeven_rate = sum(1 for trade in items if trade.breakeven_stop_hit) / len(items) * 100
        target_rate = sum(1 for trade in items if trade.target_hit) / len(items) * 100
        rows.append(
            f"| {factor} | {len(items)} | {fmt_pct(mean(returns))} | {fmt_pct(median(returns))} | "
            f"{win_rate(returns):.1f}% | {stop_rate:.1f}% | {breakeven_rate:.1f}% | {target_rate:.1f}% |"
        )
    return rows


def build_slice_report(
    trades: list[BacktestTrade],
    all_candidates_count: int,
    universe_size: int,
    target_date: dt.date,
    hold_days: int,
    top: int,
    breakeven_trigger_pct: float | None,
    errors: list[str],
) -> str:
    lines = [
        "# A股裸K策略切片回测",
        "",
        f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 参数",
        "",
        f"- 目标切片日：{target_date.isoformat()}（若非交易日，使用该日前最近一个交易日）",
        "- 选股口径：当前 a-share-analyse 严格做多候选规则",
        f"- 排序口径：看涨置信度从高到低，取前 {top} 只",
        f"- 入场规则：信号日不买入；次日回踩突破价附近且不破位才买入",
        "- 止损规则：优先使用候选生成时的结构止损；无有效结构止损时才退回入场价下方5%",
        f"- 持有检验：实际入场后第 {hold_days} 个交易日收盘",
        f"- 保本移动止损：{'浮盈达到 ' + str(breakeven_trigger_pct) + '% 后抬到入场价' if breakeven_trigger_pct is not None else '关闭'}",
        f"- 股票池：当前可获取的沪深主板流动性股票，共 {universe_size} 只",
        "",
        "## 结果摘要",
        "",
    ]
    if trades:
        returns = [trade.return_pct for trade in trades]
        lines.extend(
            [
                f"- 当日满足严格做多规则的股票：{all_candidates_count} 只",
                f"- 实际纳入回测：{len(trades)} 只",
                f"- 平均{hold_days}日涨幅：{fmt_pct(mean(returns))}",
                f"- 中位数{hold_days}日涨幅：{fmt_pct(median(returns))}",
                f"- 胜率：{win_rate(returns):.1f}%",
                f"- 最好：{max(trades, key=lambda item: item.return_pct).code} {max(trades, key=lambda item: item.return_pct).name} {fmt_pct(max(returns))}",
                f"- 最差：{min(trades, key=lambda item: item.return_pct).code} {min(trades, key=lambda item: item.return_pct).name} {fmt_pct(min(returns))}",
            ]
        )
    else:
        lines.extend([f"- 当日满足严格做多规则的股票：{all_candidates_count} 只", "- 没有可用于收益检验的候选。"])
    lines.extend(["", "## 明细", ""])
    lines.extend(trade_detail_rows(trades))
    add_caveats(lines, errors)
    return "\n".join(lines) + "\n"


def trade_detail_rows(trades: list[BacktestTrade], limit: int = 80) -> list[str]:
    rows = [
        "| 代码 | 名称 | 信号日 | 入场日 | 持有 | 退出日 | 入场 | 退出 | 收益 | 超额沪深300 | MFE | MAE | 结构止损 | 保本止损 | 目标 | 置信度 | 形态 | 信号 | 入场规则 | 退出规则 | 盈亏比 | 看涨因子 |",
        "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|---|---|---|---|---:|---|",
    ]
    for trade in trades[:limit]:
        factors = "、".join(trade.confidence_factors) if trade.confidence_factors else "-"
        signals = "+".join(trade.signals) if trade.signals else "-"
        excess = trade.return_pct - trade.benchmark_return_pct if trade.benchmark_return_pct is not None else None
        rows.append(
            f"| {trade.code} | {trade.name} | {trade.signal_date} | {trade.entry_date} | {trade.hold_days} | {trade.exit_date} | "
            f"{trade.entry_close:.2f} | {trade.exit_close:.2f} | {fmt_pct(trade.return_pct)} | {fmt_pct(excess)} | "
            f"{fmt_pct(trade.mfe_pct)} | {fmt_pct(trade.mae_pct)} | {'是' if trade.stop_hit else '否'} | "
            f"{'是' if trade.breakeven_stop_hit else '否'} | "
            f"{'是' if trade.target_hit else '否'} | {trade.bullish_confidence:.0f}% | {trade.setup} | {signals} | "
            f"{trade.entry_rule} | {trade.exit_rule} | {trade.reward_risk:.2f} | {factors} |"
        )
    return rows


def add_caveats(lines: list[str], errors: list[str]) -> None:
    lines.extend(
        [
            "",
            "## 备注",
            "",
            "- 这是规则回放，不含交易费用、滑点、涨跌停无法成交、停牌等执行约束。",
            "- 股票池使用当前可获取股票列表，会有幸存者偏差和当前流动性筛选偏差。",
            "- K线使用前复权公共数据源；历史复权数据可能随分红送转被重算。",
            "- 滚动回测按固定间隔抽样，可能同一股票在不同信号日重复入选。",
        ]
    )
    if errors:
        lines.extend(["", "## 数据错误样例", ""])
        for error in errors[:20]:
            lines.append(f"- {error}")


def build_rolling_report(
    trades: list[BacktestTrade],
    by_date: dict[str, list[CandidateAtDate]],
    sample_dates: list[dt.date],
    universe_size: int,
    lookback_days: int,
    sample_step: int,
    top: int,
    hold_days_list: list[int],
    breakeven_trigger_pct: float | None,
    errors: list[str],
) -> str:
    primary_hold = 10 if 10 in hold_days_list else hold_days_list[0]
    primary_trades = [trade for trade in trades if trade.hold_days == primary_hold]
    returns = [trade.return_pct for trade in primary_trades]
    signal_counts = [len(by_date.get(date.isoformat(), [])) for date in sample_dates]
    lines = [
        "# A股裸K策略滚动回测",
        "",
        f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 参数",
        "",
        f"- 回测窗口：过去 {lookback_days} 个自然日",
        f"- 抽样频率：每 {sample_step} 个交易日取一次信号",
        f"- 每期选股：看涨置信度最高的前 {top} 只严格做多候选",
        "- 入场规则：信号日不买入；次日回踩突破价附近且不破位才买入",
        "- 止损规则：优先使用候选生成时的结构止损；无有效结构止损时才退回入场价下方5%",
        f"- 持有检验：实际入场后 {', '.join(str(day) + '日' for day in hold_days_list)}",
        f"- 保本移动止损：{'浮盈达到 ' + str(breakeven_trigger_pct) + '% 后抬到入场价' if breakeven_trigger_pct is not None else '关闭'}",
        f"- 股票池：当前可获取的沪深主板流动性股票，共 {universe_size} 只",
        f"- 实际信号日数量：{len(sample_dates)}",
        "",
        "## 总览",
        "",
        f"- 每期平均严格候选数：{mean(signal_counts):.2f} 只",
        f"- 每期中位严格候选数：{median(signal_counts):.2f} 只",
        f"- {primary_hold}日样本数：{len(primary_trades)} 笔",
        f"- {primary_hold}日平均收益：{fmt_pct(mean(returns))}",
        f"- {primary_hold}日中位收益：{fmt_pct(median(returns))}",
        f"- {primary_hold}日胜率：{win_rate(returns):.1f}%",
        "",
        "## 持有周期表现",
        "",
    ]
    lines.extend(metric_rows(trades, hold_days_list))
    lines.extend(["", f"## 因子归因（{primary_hold}日）", ""])
    lines.extend(factor_rows(trades, primary_hold, min_count=2))
    lines.extend(["", f"## {primary_hold}日交易明细（前80笔）", ""])
    detail = sorted(primary_trades, key=lambda trade: (trade.signal_date, -trade.bullish_confidence, trade.code))
    lines.extend(trade_detail_rows(detail, limit=80))
    add_caveats(lines, errors)
    return "\n".join(lines) + "\n"


def load_all_bars(universe: list[dict[str, Any]], workers: int) -> tuple[list[QuoteBars], list[str]]:
    loaded: list[QuoteBars] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch_quote_bars, quote) for quote in universe]
        for index, future in enumerate(as_completed(futures), 1):
            quote_bars, error = future.result()
            if quote_bars:
                loaded.append(quote_bars)
            if error:
                errors.append(error)
            if index % 500 == 0:
                print(f"  loaded {index}/{len(universe)}; usable={len(loaded)}", flush=True)
    return loaded, errors


def run_slice_backtest(
    *,
    top: int,
    workers: int,
    min_amount: float,
    min_buy_sell_ratio: float,
    as_of: dt.date,
    days_ago: int,
    hold_days: int,
    breakeven_trigger_pct: float | None,
    report_dir: Path,
) -> Path:
    target_date = as_of - dt.timedelta(days=days_ago)
    print("Loading A-share universe...", flush=True)
    universe = agent.load_universe(min_amount)
    print(f"Loading daily bars for {len(universe)} stocks...", flush=True)
    loaded, errors = load_all_bars(universe, workers)

    by_date = collect_candidates_for_dates(loaded, [target_date], min_buy_sell_ratio)
    key = target_date.isoformat()
    candidates = sorted(by_date.get(key, []), key=rank_candidate, reverse=True)
    bench_by_date = {bar.date: bar for bar in benchmark_bars()}
    trades = [
        trade
        for item in candidates[:top]
        if (trade := make_trade(item, hold_days, bench_by_date, breakeven_trigger_pct)) is not None
    ]
    report = build_slice_report(trades, len(candidates), len(universe), target_date, hold_days, top, breakeven_trigger_pct, errors)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"a_share_backtest_{target_date.isoformat()}_{hold_days}d.md"
    path.write_text(report, encoding="utf-8")
    (report_dir / "a_share_backtest_latest.md").write_text(report, encoding="utf-8")
    return path


def run_rolling_backtest(
    *,
    top: int,
    workers: int,
    min_amount: float,
    min_buy_sell_ratio: float,
    as_of: dt.date,
    lookback_days: int,
    sample_step: int,
    hold_days_list: list[int],
    breakeven_trigger_pct: float | None,
    report_dir: Path,
) -> Path:
    print("Loading A-share universe...", flush=True)
    universe = agent.load_universe(min_amount)
    print(f"Loading daily bars for {len(universe)} stocks...", flush=True)
    loaded, errors = load_all_bars(universe, workers)
    if not loaded:
        raise RuntimeError("No usable daily bars loaded.")

    reference = max((item.bars for item in loaded), key=len)
    sample_dates = sample_dates_from_reference(
        reference,
        as_of=as_of,
        lookback_days=lookback_days,
        sample_step=sample_step,
        max_hold_days=max(hold_days_list),
    )
    print(f"Scoring {len(sample_dates)} sampled signal dates...", flush=True)
    by_date = collect_candidates_for_dates(loaded, sample_dates, min_buy_sell_ratio)
    bench_by_date = {bar.date: bar for bar in benchmark_bars()}

    trades: list[BacktestTrade] = []
    for signal_date in sample_dates:
        candidates = sorted(by_date.get(signal_date.isoformat(), []), key=rank_candidate, reverse=True)[:top]
        for item in candidates:
            for hold_days in hold_days_list:
                trade = make_trade(item, hold_days, bench_by_date, breakeven_trigger_pct)
                if trade:
                    trades.append(trade)

    report = build_rolling_report(
        trades=trades,
        by_date=by_date,
        sample_dates=sample_dates,
        universe_size=len(universe),
        lookback_days=lookback_days,
        sample_step=sample_step,
        top=top,
        hold_days_list=hold_days_list,
        breakeven_trigger_pct=breakeven_trigger_pct,
        errors=errors,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"a_share_rolling_backtest_{lookback_days}d_step{sample_step}.md"
    path.write_text(report, encoding="utf-8")
    (report_dir / "a_share_rolling_backtest_latest.md").write_text(report, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest current A-share skill.")
    parser.add_argument("--mode", choices=["slice", "rolling"], default="slice")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--min-amount", type=float, default=80_000_000)
    parser.add_argument("--min-buy-sell-ratio", type=float, default=2.0)
    parser.add_argument("--as-of", default=None, help="Anchor date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--days-ago", type=int, default=30)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--hold-days-list", default="5,10,20")
    parser.add_argument(
        "--breakeven-trigger-pct",
        type=float,
        default=None,
        help="Move stop to entry price after this intraperiod MFE percentage is reached.",
    )
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--sample-step", type=int, default=5)
    parser.add_argument(
        "--report-dir",
        default="F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/a-share/backtests",
    )
    args = parser.parse_args()

    if args.mode == "rolling":
        path = run_rolling_backtest(
            top=args.top,
            workers=args.workers,
            min_amount=args.min_amount,
            min_buy_sell_ratio=args.min_buy_sell_ratio,
            as_of=parse_date(args.as_of),
            lookback_days=args.lookback_days,
            sample_step=args.sample_step,
            hold_days_list=parse_hold_days(args.hold_days_list),
            breakeven_trigger_pct=args.breakeven_trigger_pct,
            report_dir=Path(args.report_dir),
        )
    else:
        path = run_slice_backtest(
            top=args.top,
            workers=args.workers,
            min_amount=args.min_amount,
            min_buy_sell_ratio=args.min_buy_sell_ratio,
            as_of=parse_date(args.as_of),
            days_ago=args.days_ago,
            hold_days=args.hold_days,
            breakeven_trigger_pct=args.breakeven_trigger_pct,
            report_dir=Path(args.report_dir),
        )
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
