#!/usr/bin/env python3
"""
Daily US stock long/short pattern scanner.

Uses Yahoo Finance's public chart endpoint. The report is research only:
it creates a watchlist with current prices and technical reasons, not trades.
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trading_strategy import Candle, TradePlan, build_trade_plan, risk_reward


NASDAQ_HISTORICAL_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"
NASDAQ_INFO_URL = "https://api.nasdaq.com/api/quote/{symbol}/info"
DEFAULT_REPORT_DIR = "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/stocks/daily"
DEFAULT_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "AVGO",
    "TSLA",
    "BRK-B",
    "JPM",
    "LLY",
    "V",
    "MA",
    "UNH",
    "XOM",
    "COST",
    "NFLX",
    "WMT",
    "ORCL",
    "HD",
    "PG",
    "JNJ",
    "BAC",
    "ABBV",
    "KO",
    "PLTR",
    "AMD",
    "CRM",
    "CSCO",
    "CVX",
    "WFC",
    "IBM",
    "GE",
    "MRK",
    "AXP",
    "NOW",
    "MCD",
    "DIS",
    "INTU",
    "GS",
    "UBER",
    "CAT",
    "QCOM",
    "TXN",
    "VZ",
    "T",
    "AMAT",
    "SPGI",
    "BKNG",
    "ISRG",
    "PFE",
    "BA",
    "UNP",
    "LOW",
    "RTX",
    "HON",
    "ADBE",
    "PANW",
    "LRCX",
    "AMGN",
    "MU",
    "DE",
    "NKE",
    "SBUX",
    "COIN",
    "MSTR",
    "SMCI",
    "ARM",
    "SHOP",
    "CRWD",
    "SNOW",
    "NET",
    "DDOG",
    "MDB",
    "RBLX",
    "ROKU",
    "SOFI",
    "HOOD",
    "UPST",
]
STOCK_NAMES = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta Platforms",
    "AVGO": "Broadcom",
    "TSLA": "Tesla",
    "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase",
    "LLY": "Eli Lilly",
    "V": "Visa",
    "MA": "Mastercard",
    "UNH": "UnitedHealth",
    "XOM": "Exxon Mobil",
    "COST": "Costco",
    "NFLX": "Netflix",
    "WMT": "Walmart",
    "ORCL": "Oracle",
    "HD": "Home Depot",
    "PG": "Procter & Gamble",
    "JNJ": "Johnson & Johnson",
    "BAC": "Bank of America",
    "ABBV": "AbbVie",
    "KO": "Coca-Cola",
    "PLTR": "Palantir",
    "AMD": "Advanced Micro Devices",
    "CRM": "Salesforce",
    "CSCO": "Cisco",
    "CVX": "Chevron",
    "WFC": "Wells Fargo",
    "IBM": "IBM",
    "GE": "GE Aerospace",
    "MRK": "Merck",
    "AXP": "American Express",
    "NOW": "ServiceNow",
    "MCD": "McDonald's",
    "DIS": "Disney",
    "INTU": "Intuit",
    "GS": "Goldman Sachs",
    "UBER": "Uber",
    "CAT": "Caterpillar",
    "QCOM": "Qualcomm",
    "TXN": "Texas Instruments",
    "VZ": "Verizon",
    "T": "AT&T",
    "AMAT": "Applied Materials",
    "SPGI": "S&P Global",
    "BKNG": "Booking Holdings",
    "ISRG": "Intuitive Surgical",
    "PFE": "Pfizer",
    "BA": "Boeing",
    "UNP": "Union Pacific",
    "LOW": "Lowe's",
    "RTX": "RTX",
    "HON": "Honeywell",
    "ADBE": "Adobe",
    "PANW": "Palo Alto Networks",
    "LRCX": "Lam Research",
    "AMGN": "Amgen",
    "MU": "Micron",
    "DE": "Deere",
    "NKE": "Nike",
    "SBUX": "Starbucks",
    "COIN": "Coinbase",
    "MSTR": "MicroStrategy",
    "SMCI": "Super Micro Computer",
    "ARM": "Arm Holdings",
    "SHOP": "Shopify",
    "CRWD": "CrowdStrike",
    "SNOW": "Snowflake",
    "NET": "Cloudflare",
    "DDOG": "Datadog",
    "MDB": "MongoDB",
    "RBLX": "Roblox",
    "ROKU": "Roku",
    "SOFI": "SoFi",
    "HOOD": "Robinhood",
    "UPST": "Upstart",
}


@dataclass(frozen=True)
class StockSignal:
    symbol: str
    name: str
    price: float
    change_1d: float | None
    change_5d: float | None
    volume: float | None
    avg_volume_20: float | None
    pattern: str
    score: float
    reasons: list[str]
    side: str
    trade_plan: TradePlan | None


def fetch_json(url: str, params: dict[str, Any], *, retries: int = 3) -> Any:
    query = urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
        "User-Agent": "Mozilla/5.0 trade-agent-stock-daily/1.0",
    }
    full_url = f"{url}?{query}"

    for attempt in range(1, retries + 1):
        request = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 502, 503, 504} and attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(3 * attempt)
                continue
            raise RuntimeError(f"request failed: {exc}") from exc

    raise RuntimeError("request failed")


def number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def market_number(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "--"}:
        return default
    cleaned = text.replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    return number(cleaned, default)


def fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:+.2f}%"


def fmt_volume(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}K"
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


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def volatility(prices: list[float], days: int = 30) -> float | None:
    if len(prices) <= days:
        return None
    returns = [
        (current - previous) / previous
        for previous, current in zip(prices[-days - 1 : -1], prices[-days:])
        if previous
    ]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252) * 100


def nasdaq_symbol(symbol: str) -> str:
    if "-" not in symbol:
        return symbol
    return symbol.replace("-", ".")


def encoded_symbol(symbol: str) -> str:
    return urllib.parse.quote(nasdaq_symbol(symbol), safe="")


def candidate_symbols(symbol: str) -> list[str]:
    candidates = [symbol]
    if "-" in symbol:
        candidates.append(symbol.replace("-", "."))
        candidates.append(symbol.replace("-", "/"))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def fetch_json_for_symbol(url_template: str, symbol: str, params: dict[str, Any], *, retries: int = 3) -> Any:
    last_error: Exception | None = None
    for candidate in candidate_symbols(symbol):
        try:
            payload = fetch_json(url_template.format(symbol=urllib.parse.quote(candidate, safe="")), params, retries=retries)
            status = payload.get("status") if isinstance(payload, dict) else None
            if isinstance(status, dict) and status.get("rCode") not in (None, 200):
                raise RuntimeError(f"API status {status.get('rCode')}")
            if isinstance(payload, dict) and payload.get("data") is None:
                raise RuntimeError("empty data")
            return payload
        except RuntimeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("request failed")


def fetch_daily_history(symbol: str) -> tuple[list[float], list[float], list[Candle]]:
    end = dt.date.today()
    start = end - dt.timedelta(days=240)
    payload = fetch_json_for_symbol(
        NASDAQ_HISTORICAL_URL,
        symbol,
        {
            "assetclass": "stocks",
            "fromdate": start.isoformat(),
            "todate": end.isoformat(),
            "limit": "9999",
        },
    )
    data = payload.get("data") or {}
    rows = data.get("tradesTable", {}).get("rows", [])
    prices: list[float] = []
    volumes: list[float] = []
    candles: list[Candle] = []
    for row in reversed(rows):
        open_price = market_number(row.get("open"))
        high = market_number(row.get("high"))
        low = market_number(row.get("low"))
        close = market_number(row.get("close"))
        volume = market_number(row.get("volume"))
        if close is not None and close > 0:
            prices.append(close)
        if volume is not None and volume >= 0:
            volumes.append(volume)
        if open_price and high and low and close:
            candles.append(Candle(open=open_price, high=high, low=low, close=close, volume=volume))
    if len(prices) < 60:
        raise RuntimeError("not enough daily history")
    return prices, volumes, candles


def fetch_latest_price(symbol: str, fallback: float) -> float:
    payload = fetch_json_for_symbol(
        NASDAQ_INFO_URL,
        symbol,
        {"assetclass": "stocks"},
    )
    data = payload.get("data") or {}
    price = market_number(data.get("primaryData", {}).get("lastSalePrice"))
    return price if price is not None and price > 0 else fallback


def score_stock(
    symbol: str,
    name: str,
    prices: list[float],
    volumes: list[float],
    candles: list[Candle],
    price: float,
) -> tuple[StockSignal, StockSignal]:
    sma_20 = sma(prices, 20)
    sma_50 = sma(prices, 50)
    sma_100 = sma(prices, 100)
    ema_12 = ema(prices, 12)
    ema_26 = ema(prices, 26)
    macd = None if ema_12 is None or ema_26 is None else ema_12 - ema_26
    rsi_14 = rsi(prices)
    high_20 = max(prices[-20:]) if len(prices) >= 20 else None
    low_20 = min(prices[-20:]) if len(prices) >= 20 else None
    high_55 = max(prices[-55:]) if len(prices) >= 55 else None
    low_55 = min(prices[-55:]) if len(prices) >= 55 else None
    avg_volume_20 = sma(volumes, 20)
    current_volume = volumes[-1] if volumes else None
    volume_ratio = (current_volume / avg_volume_20) if current_volume and avg_volume_20 else None
    change_1d = pct_change(price, prices[-2] if len(prices) >= 2 else None)
    change_5d = pct_change(price, prices[-6] if len(prices) >= 6 else None)
    realized_vol = volatility(prices)

    long_score = 0.0
    short_score = 0.0
    long_reasons: list[str] = []
    short_reasons: list[str] = []

    if sma_20 and sma_50 and price > sma_20 > sma_50:
        long_score += 30
        long_reasons.append("价格站上并维持在20/50日均线多头结构上方")
    if sma_20 and sma_50 and price < sma_20 < sma_50:
        short_score += 30
        short_reasons.append("价格跌在20/50日均线空头结构下方")
    if sma_50 and sma_100 and sma_50 > sma_100:
        long_score += 10
        long_reasons.append("50日均线高于100日均线")
    if sma_50 and sma_100 and sma_50 < sma_100:
        short_score += 10
        short_reasons.append("50日均线低于100日均线")
    if high_20 and price >= high_20 * 0.995:
        long_score += 24
        long_reasons.append("正在测试或突破20日高点")
    if low_20 and price <= low_20 * 1.005:
        short_score += 24
        short_reasons.append("正在测试或跌破20日低点")
    if high_55 and price >= high_55 * 0.995:
        long_score += 16
        long_reasons.append("接近55日突破位")
    if low_55 and price <= low_55 * 1.005:
        short_score += 16
        short_reasons.append("接近55日跌破位")
    if macd and macd > 0:
        long_score += 10
        long_reasons.append("MACD为正，趋势动能偏多")
    if macd and macd < 0:
        short_score += 10
        short_reasons.append("MACD为负，趋势动能偏空")
    if rsi_14:
        if 45 <= rsi_14 <= 72:
            long_score += 10
            long_reasons.append(f"RSI {rsi_14:.0f}，动能偏强但未极端超买")
        if 28 <= rsi_14 <= 55:
            short_score += 10
            short_reasons.append(f"RSI {rsi_14:.0f}，仍有下行延续空间")
        if rsi_14 > 78:
            short_score += 6
            short_reasons.append(f"RSI {rsi_14:.0f} 已过热，回落风险升高")
    if volume_ratio:
        if volume_ratio >= 1.2:
            long_score += 8
            short_score += 8
            long_reasons.append(f"成交量为20日均量的 {volume_ratio:.1f} 倍")
            short_reasons.append(f"成交量为20日均量的 {volume_ratio:.1f} 倍")
        elif volume_ratio < 0.7:
            long_score -= 5
            short_score -= 5
    if change_5d is not None:
        if change_5d > 2:
            long_score += min(12, change_5d)
            long_reasons.append(f"5日动量为 {fmt_pct(change_5d)}")
        if change_5d < -2:
            short_score += min(12, abs(change_5d))
            short_reasons.append(f"5日动量为 {fmt_pct(change_5d)}")
    if realized_vol and realized_vol > 80:
        long_score -= 5
        short_score -= 5

    long_pattern = "多头突破" if high_20 and price >= high_20 * 0.995 else "多头趋势延续"
    short_pattern = "空头跌破" if low_20 and price <= low_20 * 1.005 else "空头趋势延续"
    long_plan = build_trade_plan(candles, "long")
    short_plan = build_trade_plan(candles, "short")

    long_signal = StockSignal(
        symbol=symbol,
        name=name,
        price=price,
        change_1d=change_1d,
        change_5d=change_5d,
        volume=current_volume,
        avg_volume_20=avg_volume_20,
        pattern=long_pattern,
        score=long_score,
        reasons=long_reasons[:4],
        side="Long",
        trade_plan=long_plan,
    )
    short_signal = StockSignal(
        symbol=symbol,
        name=name,
        price=price,
        change_1d=change_1d,
        change_5d=change_5d,
        volume=current_volume,
        avg_volume_20=avg_volume_20,
        pattern=short_pattern,
        score=short_score,
        reasons=short_reasons[:4],
        side="Short",
        trade_plan=short_plan,
    )
    return long_signal, short_signal


def load_universe(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_UNIVERSE
    content = Path(path).read_text(encoding="utf-8")
    tickers: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tickers.extend(part.strip().upper() for part in line.split(",") if part.strip())
    return tickers


def process_symbol(symbol: str) -> tuple[StockSignal | None, StockSignal | None, str | None]:
    try:
        prices, volumes, candles = fetch_daily_history(symbol)
        price = prices[-1]
        name = STOCK_NAMES.get(symbol, symbol)
        long_signal, short_signal = score_stock(symbol, name, prices, volumes, candles, price)
        return long_signal, short_signal, None
    except Exception as exc:
        return None, None, f"{symbol}: {exc}"


def scan_stocks(symbols: list[str], limit: int, workers: int) -> tuple[list[StockSignal], list[StockSignal], list[str]]:
    long_signals: list[StockSignal] = []
    short_signals: list[StockSignal] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process_symbol, symbol) for symbol in symbols]
        for future in as_completed(futures):
            long_signal, short_signal, error = future.result()
            if long_signal:
                long_signals.append(long_signal)
            if short_signal:
                short_signals.append(short_signal)
            if error:
                errors.append(error)

    final_longs = sorted(long_signals, key=lambda item: item.score, reverse=True)[:limit]
    final_shorts = sorted(short_signals, key=lambda item: item.score, reverse=True)[:limit]
    return refresh_selected_prices(final_longs), refresh_selected_prices(final_shorts), errors


def refresh_selected_prices(signals: list[StockSignal]) -> list[StockSignal]:
    refreshed: list[StockSignal] = []
    for signal in signals:
        try:
            latest_price = fetch_latest_price(signal.symbol, signal.price)
            plan = adjust_plan_entry(signal.trade_plan, latest_price)
            refreshed.append(replace(signal, price=latest_price, trade_plan=plan))
            time.sleep(0.08)
        except Exception:
            refreshed.append(signal)
    return refreshed


def adjust_plan_entry(plan: TradePlan | None, entry: float) -> TradePlan | None:
    if plan is None:
        return None
    stop = entry * 0.95 if plan.side == "long" else entry * 1.05
    rr = risk_reward(entry, stop, plan.target, plan.side)
    if rr > 2 and plan.confidence >= 70:
        decision = "可交易"
    elif rr > 2:
        decision = "观察-置信度不足"
    else:
        decision = "不交易-盈亏比不足"
    return replace(plan, entry=entry, stop=stop, risk_reward=rr, decision=decision)


def signal_row(signal: StockSignal) -> str:
    reasons = "; ".join(signal.reasons) if signal.reasons else signal.pattern
    plan = signal.trade_plan
    if plan:
        trade = (
            f"{plan.decision}; 入场 {fmt_price(plan.entry)}; 止损 {fmt_price(plan.stop)}; "
            f"目标 {fmt_price(plan.target)}; R/R {plan.risk_reward:.2f}; 置信度 {plan.confidence}"
        )
        reasons = "; ".join(plan.reasons[:4])
    else:
        trade = "数据不足"
    return (
        f"| {signal.symbol} | {signal.name} | {fmt_price(signal.price)} | "
        f"{fmt_pct(signal.change_1d)} | {fmt_pct(signal.change_5d)} | "
        f"{signal.pattern} | {signal.score:.0f} | {trade} | {reasons} |"
    )


def build_report(longs: list[StockSignal], shorts: list[StockSignal], errors: list[str]) -> str:
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    lines = [
        "# 每日美股多空形态报告",
        "",
        f"Generated: {now:%Y-%m-%d %H:%M %Z}",
        "",
        "这是技术形态研究，不是投资建议，也不是自动交易信号。",
        "",
        "## 快速结论",
        "",
    ]

    if longs:
        top_long = longs[0]
        lines.append(
            f"- 最适合观察做多：{top_long.symbol}，现价 {fmt_price(top_long.price)}，"
            f"{top_long.pattern}，评分 {top_long.score:.0f}。"
        )
    else:
        lines.append("- 最适合观察做多：今天没有明显多头结构。")

    if shorts:
        top_short = shorts[0]
        lines.append(
            f"- 最适合观察做空：{top_short.symbol}，现价 {fmt_price(top_short.price)}，"
            f"{top_short.pattern}，评分 {top_short.score:.0f}。"
        )
    else:
        lines.append("- 最适合观察做空：今天没有明显空头结构。")

    lines.extend(
        [
            "",
            "## 适合做多观察",
            "",
            "| 股票 | 公司 | 现价 | 1日 | 5日 | 形态 | 评分 | 交易计划 | 理由 |",
            "|---|---|---:|---:|---:|---|---:|---|---|",
        ]
    )
    if longs:
        lines.extend(signal_row(signal) for signal in longs)
    else:
        lines.append("| - | - | - | - | - | - | - | - | 今天没有明显多头结构。 |")

    lines.extend(
        [
            "",
            "## 适合做空观察",
            "",
            "| 股票 | 公司 | 现价 | 1日 | 5日 | 形态 | 评分 | 交易计划 | 理由 |",
            "|---|---|---:|---:|---:|---|---:|---|---|",
        ]
    )
    if shorts:
        lines.extend(signal_row(signal) for signal in shorts)
    else:
        lines.append("| - | - | - | - | - | - | - | - | 今天没有明显空头结构。 |")

    lines.extend(
        [
            "",
            "## 方法",
            "",
            (
                "扫描器按日线结构给美股排序：价格相对20/50/100日均线的位置、20日和55日突破/跌破、"
                "MACD方向、RSI、5日动量，以及成交量相对20日均量的变化。交易计划按当前价入场、"
                "固定5%止损；做多目标为压力位，做空目标为支撑位；只有盈亏比 > 2 且置信度 >= 70 才标记为可交易。"
            ),
        ]
    )

    if errors:
        lines.extend(["", "## 数据备注", ""])
        lines.extend(f"- {error}" for error in errors[:10])
        if len(errors) > 10:
            lines.append(f"- {len(errors) - 10} more symbols had data issues.")

    return "\n".join(lines) + "\n"


def write_report(report: str, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    dated_path = report_dir / f"stock_daily_{stamp}.md"
    latest_path = report_dir / "stock_latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    return dated_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a US stock long/short pattern report.")
    parser.add_argument("--top", type=int, default=8, help="Number of long and short candidates to show.")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent Nasdaq history requests.")
    parser.add_argument("--universe-file", help="Optional file of comma/newline-separated tickers.")
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for Markdown reports.")
    args = parser.parse_args()

    try:
        symbols = load_universe(args.universe_file)
        longs, shorts, errors = scan_stocks(symbols, args.top, args.workers)
        report = build_report(longs, shorts, errors)
        report_path = write_report(report, Path(args.report_dir))
        print(f"Wrote {report_path}")
        if longs:
            print(f"Top long: {longs[0].symbol} score={longs[0].score:.0f}")
        if shorts:
            print(f"Top short: {shorts[0].symbol} score={shorts[0].score:.0f}")
        return 0
    except Exception as exc:
        print(f"stock_daily_agent failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
