#!/usr/bin/env python3
"""
Daily crypto heat and pattern analysis agent.

Uses Coinbase public market data endpoints, so it can run without local secrets.
The output is a Markdown report in reports/ plus a latest.md pointer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trading_strategy import Candle, TradePlan, build_trade_plan, risk_reward


COINBASE_API_BASE = "https://api.coinbase.com/v2"
COINBASE_EXCHANGE_BASE = "https://api.exchange.coinbase.com"
DEFAULT_VS_CURRENCY = "usd"
DEFAULT_REPORT_DIR = "reports"
STABLE_SYMBOLS = {
    "usdt",
    "usdc",
    "dai",
    "fdusd",
    "tusd",
    "usdd",
    "usde",
    "usds",
    "pyusd",
    "gusd",
    "lusd",
    "frax",
    "usd1",
    "rlusd",
    "rusd",
}
WRAPPED_SYMBOLS = {"wbtc", "weth", "steth", "weeth", "wsteth", "cbeth", "reth"}


@dataclass(frozen=True)
class HeatCandidate:
    coin_id: str
    product_id: str | None
    symbol: str
    name: str
    price: float | None
    market_cap_rank: int | None
    volume_24h: float
    market_cap: float
    change_1h: float
    change_24h: float
    change_7d: float
    trend_rank: int | None
    heat_score: float


def fetch_json_url(url: str, label: str, *, retries: int = 5) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "trade-agent-crypto-daily/1.0",
    }

    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else min(60, 10 * attempt)
                print(f"Rate limited by data source; retrying {label} in {wait_seconds}s...", file=sys.stderr)
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(f"Data source HTTP {exc.code} for {label}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(f"Data source request failed for {label}: {exc}") from exc

    raise RuntimeError(f"Data source request failed for {label}")


def fetch_coinbase_api(path: str, params: dict[str, Any] | None = None, *, retries: int = 5) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{COINBASE_API_BASE}{path}{query}"
    return fetch_json_url(url, path, retries=retries)


def fetch_coinbase_exchange(path: str, params: dict[str, Any] | None = None, *, retries: int = 5) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{COINBASE_EXCHANGE_BASE}{path}{query}"
    return fetch_json_url(url, path, retries=retries)


def number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"${value / 1_000:.2f}K"
    return f"${value:.4g}"


def fmt_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:+.2f}%"


def fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:.8f}".rstrip("0").rstrip(".")


def fmt_rank(value: int | None) -> str:
    return f"#{value}" if value else "-"


def fmt_heat(value: float) -> str:
    return f"{value:.0f}"


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def is_tradeable_market(row: dict[str, Any]) -> bool:
    symbol = str(row.get("symbol", "")).lower()
    price = number(row.get("current_price"))
    if symbol in STABLE_SYMBOLS or symbol in WRAPPED_SYMBOLS:
        return False
    if 0.98 <= price <= 1.02 and symbol.endswith("usd"):
        return False
    return number(row.get("total_volume")) >= 50_000_000


def get_usd_products() -> dict[str, str]:
    payload = fetch_coinbase_exchange("/products")
    product_by_symbol: dict[str, str] = {}
    for item in payload:
        if str(item.get("quote_currency", "")).upper() != "USD":
            continue
        if item.get("trading_disabled") or str(item.get("status", "")).lower() != "online":
            continue
        symbol = str(item.get("base_currency", "")).upper()
        product_id = str(item.get("id", ""))
        if symbol and product_id:
            product_by_symbol[symbol] = product_id
    return product_by_symbol


def get_markets(vs_currency: str, pages: int = 1, per_page: int = 100) -> tuple[list[dict[str, Any]], list[str]]:
    if vs_currency.lower() != "usd":
        raise RuntimeError("Coinbase public market scan currently supports USD only")

    product_by_symbol = get_usd_products()
    target_count = max(1, pages * per_page)
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    starting_after: str | None = None

    while len(rows) < target_count:
        limit = min(50, target_count - len(rows))
        params: dict[str, Any] = {
            "base": "USD",
            "filter": "listed",
            "limit": limit,
        }
        if starting_after:
            params["starting_after"] = starting_after
        payload = fetch_coinbase_api("/assets/search", params)
        assets = payload.get("data", [])
        pagination = payload.get("pagination", {})
        if not assets:
            break

        for asset in assets:
            symbol = str(asset.get("symbol", "")).upper()
            latest_price = asset.get("latest_price", {})
            amount = latest_price.get("amount", {})
            rows.append(
                {
                    "id": str(asset.get("slug") or symbol.lower()),
                    "product_id": product_by_symbol.get(symbol),
                    "symbol": symbol,
                    "name": str(asset.get("name", symbol)),
                    "current_price": number(amount.get("amount") or asset.get("latest"), default=float("nan")),
                    "market_cap_rank": asset.get("rank"),
                    "total_volume": number(asset.get("volume_24h")),
                    "market_cap": number(asset.get("market_cap")),
                    "price_change_percentage_1h_in_currency": number(latest_price.get("percent_change", {}).get("hour")) * 100,
                    "price_change_percentage_24h_in_currency": number(latest_price.get("percent_change", {}).get("day")) * 100,
                    "price_change_percentage_7d_in_currency": number(latest_price.get("percent_change", {}).get("week")) * 100,
                }
            )
        next_cursor = pagination.get("next_starting_after")
        if not next_cursor:
            break
        starting_after = str(next_cursor)
        time.sleep(0.5)

    missing_products = sorted({str(row["symbol"]) for row in rows if not row.get("product_id")})
    if missing_products:
        sample = ", ".join(missing_products[:10])
        notes.append(f"Coinbase Exchange USD K线缺失，以下资产仅保留现货热度不参与短线计划：{sample}")
    return rows[:target_count], notes


def heat_score(row: dict[str, Any], trend_rank: int | None) -> float:
    change_1h = number(row.get("price_change_percentage_1h_in_currency"))
    change_24h = number(row.get("price_change_percentage_24h_in_currency"))
    change_7d = number(row.get("price_change_percentage_7d_in_currency"))
    volume = max(number(row.get("total_volume")), 1)
    market_cap = max(number(row.get("market_cap")), 1)
    rank = int(row.get("market_cap_rank") or 999)

    trend_component = 0 if trend_rank is None else max(0, 16 - trend_rank) * 9
    liquidity_component = min(35, math.log10(volume) * 4)
    turnover_component = min(30, (volume / market_cap) * 100)
    momentum_component = max(-20, min(55, change_24h * 1.5 + change_7d * 0.45 + change_1h * 0.8))
    quality_component = max(0, 22 - math.log10(max(rank, 1)) * 7)

    return trend_component + liquidity_component + turnover_component + momentum_component + quality_component


def build_heat_list(markets: list[dict[str, Any]], trend_rank: dict[str, int], limit: int) -> list[HeatCandidate]:
    candidates: list[HeatCandidate] = []
    seen: set[str] = set()
    for row in markets:
        coin_id = str(row.get("id", ""))
        if not coin_id or coin_id in seen or not is_tradeable_market(row):
            continue
        seen.add(coin_id)
        rank = trend_rank.get(coin_id)
        score = heat_score(row, rank)
        candidates.append(
            HeatCandidate(
                coin_id=coin_id,
                product_id=str(row.get("product_id")) if row.get("product_id") else None,
                symbol=str(row.get("symbol", "")).upper(),
                name=str(row.get("name", coin_id)),
                price=number(row.get("current_price"), default=float("nan")),
                market_cap_rank=row.get("market_cap_rank"),
                volume_24h=number(row.get("total_volume")),
                market_cap=number(row.get("market_cap")),
                change_1h=number(row.get("price_change_percentage_1h_in_currency")),
                change_24h=number(row.get("price_change_percentage_24h_in_currency")),
                change_7d=number(row.get("price_change_percentage_7d_in_currency")),
                trend_rank=rank,
                heat_score=score,
            )
        )
    return sorted(candidates, key=lambda item: item.heat_score, reverse=True)[:limit]


def fetch_exchange_candles(product_id: str, granularity: int, lookback_seconds: int) -> list[list[Any]]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(seconds=lookback_seconds)
    payload = fetch_coinbase_exchange(
        f"/products/{product_id}/candles",
        {
            "granularity": granularity,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
    )
    rows = [item for item in payload if len(item) >= 6]
    return sorted(rows, key=lambda item: item[0])


def fetch_market_chart(product_id: str, days: int) -> tuple[list[float], list[float]]:
    payload = fetch_exchange_candles(product_id, 86400, days * 86400 + 86400)
    prices = [number(point[4]) for point in payload]
    volumes = [number(point[5]) for point in payload]
    return prices, volumes


def fetch_ohlc(product_id: str, granularity: int = 900, lookback_candles: int = 320) -> list[Candle]:
    payload = fetch_exchange_candles(product_id, granularity, granularity * lookback_candles)
    candles: list[Candle] = []
    for item in payload:
        if len(item) >= 6:
            candles.append(
                Candle(
                    open=number(item[3]),
                    high=number(item[2]),
                    low=number(item[1]),
                    close=number(item[4]),
                )
            )
    return candles


def resample_candles(candles: list[Candle], group_size: int) -> list[Candle]:
    if group_size <= 1:
        return candles
    grouped: list[Candle] = []
    for index in range(0, len(candles), group_size):
        chunk = candles[index : index + group_size]
        if len(chunk) < group_size:
            continue
        grouped.append(
            Candle(
                open=chunk[0].open,
                high=max(candle.high for candle in chunk),
                low=min(candle.low for candle in chunk),
                close=chunk[-1].close,
            )
        )
    return grouped


def adjust_plan_entry(plan: TradePlan | None, entry: float | None) -> TradePlan | None:
    if plan is None or entry is None or math.isnan(entry):
        return plan
    stop = entry * 0.95 if plan.side == "long" else entry * 1.05
    rr = risk_reward(entry, stop, plan.target, plan.side)
    if rr > 2 and plan.confidence >= 70:
        decision = "可交易"
    elif rr > 2:
        decision = "观察-置信度不足"
    else:
        decision = "不交易-盈亏比不足"
    return replace(plan, entry=entry, stop=stop, risk_reward=rr, decision=decision)


def build_crypto_trade_plans(
    candidate: HeatCandidate,
    vs_currency: str,
) -> tuple[dict[str, dict[str, TradePlan | None]], str | None]:
    try:
        del vs_currency
        if not candidate.product_id:
            return {}, "Coinbase Exchange USD K线不可用"

        candles_15m = fetch_ohlc(candidate.product_id, granularity=900, lookback_candles=180)
        candles_1h = fetch_ohlc(candidate.product_id, granularity=3600, lookback_candles=160)
        if len(candles_15m) < 30 and len(candles_1h) < 30:
            return {}, "OHLC candles unavailable or insufficient"

        timeframes = {
            "15m": candles_15m,
            "1h": candles_1h,
            "4h近似": resample_candles(candles_1h, 4),
        }
        plans: dict[str, dict[str, TradePlan | None]] = {}
        for timeframe, candles in timeframes.items():
            if len(candles) < 30:
                continue
            long_plan = adjust_plan_entry(build_trade_plan(candles, "long"), candidate.price)
            short_plan = adjust_plan_entry(build_trade_plan(candles, "short"), candidate.price)
            plans[timeframe] = {"long": long_plan, "short": short_plan}
        return plans, None
    except Exception as exc:
            return {}, f"OHLC unavailable: {exc}"


def classify_pattern(prices: list[float], volumes: list[float]) -> dict[str, Any]:
    last = prices[-1] if prices else None
    sma_20 = sma(prices, 20)
    sma_50 = sma(prices, 50)
    ema_12 = ema(prices, 12)
    ema_26 = ema(prices, 26)
    macd = None if ema_12 is None or ema_26 is None else ema_12 - ema_26
    rsi_14 = rsi(prices)
    high_20 = max(prices[-20:]) if len(prices) >= 20 else None
    low_20 = min(prices[-20:]) if len(prices) >= 20 else None
    volume_7 = sma(volumes, 7)
    volume_30 = sma(volumes, 30)
    volume_change = percent_change(volume_7, volume_30)

    daily_returns = [
        (current - previous) / previous
        for previous, current in zip(prices[-31:-1], prices[-30:])
        if previous
    ]
    volatility = statistics.pstdev(daily_returns) * math.sqrt(365) * 100 if len(daily_returns) >= 2 else None

    if last is None:
        state = "insufficient data"
    elif sma_20 and sma_50 and last > sma_20 > sma_50 and (macd or 0) > 0:
        state = "uptrend continuation"
    elif sma_20 and sma_50 and last < sma_20 < sma_50 and (macd or 0) < 0:
        state = "downtrend continuation"
    elif high_20 and last >= high_20 * 0.995:
        state = "20-day breakout test"
    elif low_20 and last <= low_20 * 1.005:
        state = "20-day breakdown risk"
    elif rsi_14 and rsi_14 >= 70:
        state = "overbought momentum"
    elif rsi_14 and rsi_14 <= 30:
        state = "oversold reversal watch"
    else:
        state = "range or transition"

    return {
        "state": state,
        "last": last,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "macd": macd,
        "rsi_14": rsi_14,
        "high_20": high_20,
        "low_20": low_20,
        "volume_change": volume_change,
        "volatility": volatility,
    }


def pattern_note(pattern: dict[str, Any]) -> str:
    state = str(pattern.get("state", "not analyzed"))
    rsi_14 = pattern.get("rsi_14")
    volume_change = pattern.get("volume_change")
    volatility = pattern.get("volatility")

    notes = [state]
    if isinstance(rsi_14, (int, float)):
        notes.append(f"RSI {rsi_14:.0f}")
    if isinstance(volume_change, (int, float)):
        notes.append(f"vol {fmt_pct(volume_change)}")
    if isinstance(volatility, (int, float)):
        notes.append(f"volatility {fmt_pct(volatility)}")
    return "; ".join(notes)


def best_plan_text(plans: dict[str, dict[str, TradePlan | None]]) -> str:
    all_plans: list[tuple[str, TradePlan]] = []
    for timeframe, side_plans in plans.items():
        for plan in side_plans.values():
            if plan:
                all_plans.append((timeframe, plan))
    if not all_plans:
        return "数据不足"
    tradable = [(timeframe, plan) for timeframe, plan in all_plans if plan.decision == "可交易"]
    timeframe, plan = max(
        tradable or all_plans,
        key=lambda item: (item[1].decision == "可交易", item[1].confidence, item[1].risk_reward),
    )
    side = "做多" if plan.side == "long" else "做空"
    return (
        f"{timeframe} {side}: {plan.decision}; 入场 {fmt_price(plan.entry)}; "
        f"止损 {fmt_price(plan.stop)}; 目标 {fmt_price(plan.target)}; "
        f"R/R {plan.risk_reward:.2f}; 置信度 {plan.confidence}"
    )


def heat_table_line(candidate: HeatCandidate, pattern: dict[str, Any]) -> str:
    return (
        f"| {candidate.symbol} | {candidate.name} | {fmt_heat(candidate.heat_score)} | "
        f"{fmt_rank(candidate.trend_rank)} | {fmt_pct(candidate.change_24h)} | "
        f"{fmt_pct(candidate.change_7d)} | {fmt_money(candidate.volume_24h)} | "
        f"{pattern.get('state', 'not analyzed')} | {best_plan_text(pattern.get('trade_plans', {}))} |"
    )


def detail_line(candidate: HeatCandidate, pattern: dict[str, Any]) -> str:
    return (
        f"- {candidate.symbol}: {fmt_price(candidate.price)}, 1h {fmt_pct(candidate.change_1h)}, "
        f"24h {fmt_pct(candidate.change_24h)}, 7d {fmt_pct(candidate.change_7d)}. "
        f"{pattern_note(pattern)}."
    )


def trade_detail_lines(candidate: HeatCandidate, pattern: dict[str, Any]) -> list[str]:
    plans = pattern.get("trade_plans", {})
    if not plans:
        return [f"- {candidate.symbol}: 短线OHLC数据不足，暂不生成交易计划。"]
    lines: list[str] = []
    for timeframe, side_plans in plans.items():
        parts: list[str] = []
        for label, side in [("做多", "long"), ("做空", "short")]:
            plan = side_plans.get(side)
            if not plan:
                continue
            reason = "；".join(plan.reasons[:3])
            parts.append(
                f"{label} {plan.decision}，入场 {fmt_price(plan.entry)}，止损 {fmt_price(plan.stop)}，"
                f"目标 {fmt_price(plan.target)}，R/R {plan.risk_reward:.2f}，置信度 {plan.confidence}（{reason}）"
            )
        if parts:
            lines.append(f"- {candidate.symbol} {timeframe}: " + "；".join(parts))
    return lines


def build_report(
    heat_list: list[HeatCandidate],
    patterns: dict[str, dict[str, Any]],
    trending_labels: list[str],
    category_labels: list[str],
    vs_currency: str,
    notes: list[str] | None = None,
) -> str:
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    lines = [
        f"# 每日加密货币热度与短线交易计划",
        "",
        f"Generated: {now:%Y-%m-%d %H:%M %Z}",
        "",
        "这是技术形态研究，不是投资建议，也不是自动交易信号。",
    ]

    if heat_list:
        leader = heat_list[0]
        pattern = patterns.get(leader.coin_id, {})
        lines.extend(
            [
                "",
                "## 快速结论",
                "",
                (
                    f"- 最热币种：{leader.symbol} ({leader.name})，热度 {fmt_heat(leader.heat_score)}，"
                    f"趋势排名 {fmt_rank(leader.trend_rank)}，24h {fmt_pct(leader.change_24h)}，"
                    f"7d {fmt_pct(leader.change_7d)}，24h成交额 {fmt_money(leader.volume_24h)}。"
                ),
                f"- 形态：{pattern_note(pattern)}。",
            ]
        )

    if len(heat_list) > 1:
        movers = sorted(heat_list, key=lambda item: item.change_24h, reverse=True)[:3]
        lines.append(
            "- 24小时最强动量："
            + ", ".join(f"{item.symbol} {fmt_pct(item.change_24h)}" for item in movers)
            + "。"
        )

    lines.extend(
        [
            "",
            "## 热度观察列表",
            "",
            "| 币种 | 名称 | 热度 | 趋势 | 24h | 7d | 成交额 | 日线形态 | 短线交易计划 |",
            "|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )

    for candidate in heat_list:
        lines.append(heat_table_line(candidate, patterns.get(candidate.coin_id, {"state": "not analyzed"})))

    lines.extend(["", "## 短线交易计划明细", ""])

    for candidate in heat_list:
        lines.extend(trade_detail_lines(candidate, patterns.get(candidate.coin_id, {"state": "not analyzed"})))

    lines.extend(["", "## 日线形态细节", ""])

    for candidate in heat_list:
        lines.append(detail_line(candidate, patterns.get(candidate.coin_id, {"state": "not analyzed"})))

    lines.extend(
        [
            "",
            "## 热门搜索",
            "",
            ", ".join(trending_labels[:15]) if trending_labels else "当前数据源未提供公开热门搜索榜单。",
            "",
            "## 热门板块",
            "",
            ", ".join(category_labels) if category_labels else "当前数据源未提供公开板块榜单。",
            "",
            "## 方法",
            "",
            (
                f"热度结合 Coinbase 公开市场数据中的 {vs_currency.upper()} 流动性、成交额/市值换手、"
                "1h/24h/7d 动量和市值排名。短线计划按当前价入场、固定5%止损；做多目标为压力位，"
                "做空目标为支撑位；只有盈亏比 > 2 且置信度 >= 70 才标记为可交易。"
            ),
        ]
    )

    if notes:
        lines.extend(["", "## 数据备注", ""])
        lines.extend(f"- {note}" for note in notes)

    return "\n".join(lines) + "\n"


def write_report(report: str, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    dated_path = report_dir / f"crypto_daily_{stamp}.md"
    latest_path = report_dir / "latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    return dated_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a daily crypto heat and pattern analysis report.")
    parser.add_argument("--top", type=int, default=8, help="Number of hot coins to analyze.")
    parser.add_argument("--days", type=int, default=90, help="Days of daily price history for pattern analysis.")
    parser.add_argument("--market-pages", type=int, default=1, help="Coinbase listed-asset pages to scan (50 assets/page).")
    parser.add_argument("--vs-currency", default=DEFAULT_VS_CURRENCY, help="Quote currency, e.g. usd.")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for Markdown reports.")
    args = parser.parse_args()

    try:
        notes: list[str] = []
        trend_rank: dict[str, int] = {}
        trending_labels: list[str] = []
        category_labels: list[str] = []
        markets, market_notes = get_markets(args.vs_currency, pages=args.market_pages)
        notes.extend(market_notes)
        heat_list = build_heat_list(markets, trend_rank, args.top)

        patterns: dict[str, dict[str, Any]] = {}
        for candidate in heat_list:
            try:
                if not candidate.product_id:
                    patterns[candidate.coin_id] = {"state": "history unavailable: Coinbase Exchange USD pair missing"}
                    continue
                prices, volumes = fetch_market_chart(candidate.product_id, args.days)
                patterns[candidate.coin_id] = classify_pattern(prices, volumes)
                trade_plans, trade_error = build_crypto_trade_plans(candidate, args.vs_currency)
                patterns[candidate.coin_id]["trade_plans"] = trade_plans
                if trade_error:
                    notes.append(f"{candidate.symbol}: {trade_error}")
                time.sleep(0.8)
            except Exception as exc:  # Keep the daily report alive if one coin's history fails.
                patterns[candidate.coin_id] = {"state": f"history unavailable: {exc}"}

        report = build_report(heat_list, patterns, trending_labels, category_labels, args.vs_currency, notes)
        report_path = write_report(report, Path(args.report_dir))
        print(f"Wrote {report_path}")
        if heat_list:
            leader = heat_list[0]
            print(f"Top heat: {leader.symbol} ({leader.name}) score={leader.heat_score:.1f}")
        return 0
    except Exception as exc:
        print(f"crypto_daily_agent failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
