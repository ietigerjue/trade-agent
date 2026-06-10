#!/usr/bin/env python3
"""
Review strict A-share candidates selected in an earlier daily report.

The review uses the same execution assumption as the backtest:
the signal day is not an entry; entry only happens after the next session
retests the breakout/support line and holds above it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import a_share_daily_agent as agent
import backtest_a_share_skill as backtest


REPORT_RE = re.compile(r"a_share_daily_(\d{4}-\d{2}-\d{2})\.md$")
CODE_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class SelectedStock:
    code: str
    name: str
    source_row: list[str]


@dataclass(frozen=True)
class ReviewResult:
    code: str
    name: str
    signal_date: str
    status: str
    entry_date: str | None
    exit_date: str | None
    entry_price: float | None
    exit_price: float | None
    return_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    stop_hit: bool
    target_hit: bool
    setup: str | None
    signals: list[str]
    reward_risk: float | None
    confidence: float | None
    note: str


def parse_date(value: str | None) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return dt.datetime.now().date()


def report_date(path: Path) -> dt.date | None:
    match = REPORT_RE.match(path.name)
    if not match:
        return None
    return dt.date.fromisoformat(match.group(1))


def find_source_report(report_dir: Path, target_date: dt.date, tolerance_days: int) -> Path:
    candidates: list[tuple[int, dt.date, Path]] = []
    for path in report_dir.glob("a_share_daily_*.md"):
        date = report_date(path)
        if date is None or date > target_date:
            continue
        age = (target_date - date).days
        if age <= tolerance_days:
            candidates.append((age, date, path))
    if not candidates:
        raise FileNotFoundError(
            f"No dated A-share report found on or before {target_date.isoformat()} "
            f"within {tolerance_days} days."
        )
    return sorted(candidates, key=lambda item: (item[0], item[1]), reverse=False)[0][2]


def section_lines(lines: list[str], heading: str) -> list[str]:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def parse_strict_candidates(report_path: Path) -> list[SelectedStock]:
    lines = report_path.read_text(encoding="utf-8").splitlines()
    rows: list[SelectedStock] = []
    for line in section_lines(lines, "## 做多候选"):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not CODE_RE.match(cells[0]):
            continue
        rows.append(SelectedStock(code=cells[0], name=cells[1], source_row=cells))
    return rows


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def fmt_float(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def fmt_pct(value: float | None) -> str:
    return "-" if value is None else pct(value)


def fetch_selected_bars(stock: SelectedStock) -> tuple[SelectedStock, list[agent.Bar] | None, str | None]:
    try:
        return stock, agent.fetch_daily_bars(stock.code), None
    except Exception as exc:
        return stock, None, f"{stock.code} {stock.name}: {exc}"


def latest_index_on_or_before(bars: list[agent.Bar], as_of: dt.date) -> int | None:
    index: int | None = None
    for i, bar in enumerate(bars):
        if dt.date.fromisoformat(bar.date) <= as_of:
            index = i
        else:
            break
    return index


def review_stock(
    stock: SelectedStock,
    bars: list[agent.Bar],
    signal_date: dt.date,
    as_of: dt.date,
    min_buy_sell_ratio: float,
) -> ReviewResult:
    signal_index = backtest.find_signal_index(bars, signal_date)
    exit_index = latest_index_on_or_before(bars, as_of)
    if signal_index is None or exit_index is None:
        return ReviewResult(
            stock.code,
            stock.name,
            signal_date.isoformat(),
            "数据不足",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            False,
            None,
            [],
            None,
            None,
            "找不到信号日或复盘日对应K线。",
        )
    if signal_index < 130:
        return ReviewResult(
            stock.code,
            stock.name,
            bars[signal_index].date,
            "数据不足",
            None,
            bars[exit_index].date,
            None,
            bars[exit_index].close,
            None,
            None,
            None,
            False,
            False,
            None,
            [],
            None,
            None,
            "信号日前历史K线不足，无法复原策略评分。",
        )

    quote: dict[str, Any] = {"code": stock.code, "name": stock.name, "amount": 0, "changepercent": None}
    history = bars[: signal_index + 1]
    candidate = agent.score_price_action(stock.code, stock.name, quote, history, min_buy_sell_ratio)
    if candidate is None:
        return ReviewResult(
            stock.code,
            stock.name,
            bars[signal_index].date,
            "规则未复原",
            None,
            bars[exit_index].date,
            None,
            bars[exit_index].close,
            None,
            None,
            None,
            False,
            False,
            None,
            [],
            None,
            None,
            "当前数据源/复权口径下未复原出当日严格候选，保留原日报选择但不计算入场。",
        )

    item = backtest.CandidateAtDate(
        code=stock.code,
        name=stock.name,
        signal_index=signal_index,
        candidate=candidate,
        bars=bars,
    )
    entry = backtest.next_day_retest_entry(item)
    if entry is None:
        return ReviewResult(
            stock.code,
            stock.name,
            bars[signal_index].date,
            "未进场",
            None,
            bars[exit_index].date,
            None,
            bars[exit_index].close,
            None,
            None,
            None,
            False,
            False,
            candidate.setup,
            candidate.signals,
            candidate.reward_risk,
            candidate.bullish_confidence,
            "信号后次日没有回踩突破位附近并站稳，按执行纪律不进场。",
        )

    entry_index, entry_price, entry_rule = entry
    if entry_index > exit_index:
        return ReviewResult(
            stock.code,
            stock.name,
            bars[signal_index].date,
            "等待复盘",
            bars[entry_index].date,
            None,
            entry_price,
            None,
            None,
            None,
            None,
            False,
            False,
            candidate.setup,
            candidate.signals,
            candidate.reward_risk,
            candidate.bullish_confidence,
            "已出现回踩确认，但复盘截止日早于入场日。",
        )

    path = bars[entry_index : exit_index + 1]
    exit_price = bars[exit_index].close
    stop_price = entry_price * 0.95
    return ReviewResult(
        stock.code,
        stock.name,
        bars[signal_index].date,
        "已进场",
        bars[entry_index].date,
        bars[exit_index].date,
        entry_price,
        exit_price,
        (exit_price - entry_price) / entry_price * 100,
        (max(bar.high for bar in path) - entry_price) / entry_price * 100,
        (min(bar.low for bar in path) - entry_price) / entry_price * 100,
        any(bar.low <= stop_price for bar in path),
        any(bar.high >= candidate.target for bar in path),
        candidate.setup,
        candidate.signals,
        candidate.reward_risk,
        candidate.bullish_confidence,
        entry_rule,
    )


def build_report(
    *,
    source_report: Path,
    signal_date: dt.date,
    as_of: dt.date,
    selected: list[SelectedStock],
    results: list[ReviewResult],
    errors: list[str],
) -> str:
    entered = [item for item in results if item.status == "已进场" and item.return_pct is not None]
    returns = [item.return_pct for item in entered if item.return_pct is not None]
    lines = [
        "# A股10天前信号复盘",
        "",
        f"Generated: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 参数",
        "",
        f"- 来源日报：{source_report.name}",
        f"- 信号日：{signal_date.isoformat()}",
        f"- 复盘截止：{as_of.isoformat()}",
        "- 复盘口径：只复盘来源日报 `做多候选`，不混入趋势观察池、自选股、策略二或T+0基金。",
        "- 入场纪律：突破信号日不进场；仅当次日回踩突破/支撑位附近且不破位时才视为入场。",
        "",
        "## 结果摘要",
        "",
        f"- 来源日报严格候选：{len(selected)} 只",
        f"- 实际回踩确认进场：{len(entered)} 只",
    ]
    if returns:
        lines.extend(
            [
                f"- 平均收益：{pct(sum(returns) / len(returns))}",
                f"- 胜率：{sum(1 for value in returns if value > 0) / len(returns) * 100:.1f}%",
                f"- 最好：{max(entered, key=lambda item: item.return_pct or -999).code} {pct(max(returns))}",
                f"- 最差：{min(entered, key=lambda item: item.return_pct or 999).code} {pct(min(returns))}",
            ]
        )
    else:
        lines.append("- 暂无符合“次日回踩确认”并可计算收益的交易。")

    lines.extend(
        [
            "",
            "## 复盘明细",
            "",
            "| 代码 | 名称 | 状态 | 信号日 | 入场日 | 复盘日 | 入场 | 复盘价 | 收益 | MFE | MAE | 固定5%止损 | 目标触达 | 置信度 | 形态 | 信号 | 盈亏比 | 备注 |",
            "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---|---:|---|---|---:|---|",
        ]
    )
    for item in results:
        lines.append(
            f"| {item.code} | {item.name} | {item.status} | {item.signal_date} | {item.entry_date or '-'} | {item.exit_date or '-'} | "
            f"{fmt_float(item.entry_price)} | {fmt_float(item.exit_price)} | {fmt_pct(item.return_pct)} | "
            f"{fmt_pct(item.mfe_pct)} | {fmt_pct(item.mae_pct)} | {'是' if item.stop_hit else '否'} | "
            f"{'是' if item.target_hit else '否'} | {fmt_float(item.confidence)} | {item.setup or '-'} | "
            f"{'+'.join(item.signals) if item.signals else '-'} | {fmt_float(item.reward_risk)} | {item.note} |"
        )

    lines.extend(
        [
            "",
            "## 操作策略复盘要点",
            "",
            "- 严格候选只说明出现突破/回踩结构；真正交易仍必须等次日回踩确认，避免把突破当天的情绪高点当成入场点。",
            "- 若次日没有回踩到突破位附近，或回踩后收盘跌回突破位下方，本轮信号按未进场处理。",
            "- 已进场样本优先检查MFE/MAE：MFE高但最终收益弱，说明止盈或移动止损规则可能需要优化；MAE接近-5%说明信号后承接不足。",
            "- 30日涨速、60日涨幅、60日买卖盘强度只用于趋势质量观察，不作为严格进场条件。",
            "",
            "## 数据备注",
            "",
            "- 这是规则回放，不含手续费、滑点、涨跌停无法成交、停牌等约束。",
            "- K线使用公共前复权数据；历史复权值可能随分红送转变化。",
        ]
    )
    if errors:
        lines.extend(["", "## 数据错误", ""])
        lines.extend(f"- {error}" for error in errors[:20])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Review strict candidates from an earlier A-share daily report.")
    parser.add_argument("--days-ago", type=int, default=10)
    parser.add_argument("--as-of", default=None, help="Review date, YYYY-MM-DD. Default: today.")
    parser.add_argument("--report-dir", default=agent.DEFAULT_REPORT_DIR)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--min-buy-sell-ratio", type=float, default=2.0)
    parser.add_argument("--tolerance-days", type=int, default=5)
    args = parser.parse_args()

    as_of = parse_date(args.as_of)
    target_date = as_of - dt.timedelta(days=args.days_ago)
    report_dir = Path(args.report_dir)
    source_report = find_source_report(report_dir, target_date, args.tolerance_days)
    signal_date = report_date(source_report) or target_date
    selected = parse_strict_candidates(source_report)

    results: list[ReviewResult] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(fetch_selected_bars, stock) for stock in selected]
        for future in as_completed(futures):
            stock, bars, error = future.result()
            if error:
                errors.append(error)
                continue
            if bars is None:
                continue
            results.append(review_stock(stock, bars, signal_date, as_of, args.min_buy_sell_ratio))

    order = {stock.code: index for index, stock in enumerate(selected)}
    results.sort(key=lambda item: order.get(item.code, 9999))
    report = build_report(
        source_report=source_report,
        signal_date=signal_date,
        as_of=as_of,
        selected=selected,
        results=results,
        errors=errors,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"a_share_10day_signal_review_{as_of.isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    (report_dir / "a_share_10day_signal_review_latest.md").write_text(report, encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
