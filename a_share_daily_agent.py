#!/usr/bin/env python3
"""
Daily A-share price-action scanner for the Trade Agent workspace.

It scans Shanghai/Shenzhen main-board stocks for bullish price-action setups,
then enriches the shortlist with broad-market context, industry/concept
strength, and lightweight company news/announcement notes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from trading_strategy import (  # 2026-06-04 裸 K risk/reward 规则
    Bar,
    FixedStopRR,
    compute_fixed_stop_rr,
    format_rr_lark_line,
    format_rr_markdown_row,
    higher_low,
    rank_best_long_candidates,
)


SINA_QUOTE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketData.getKLineData"
SINA_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_GUBA_URL = "https://guba.eastmoney.com/list,{code},f_1.html"
CFI_QUOTE_URL = "https://quote.cfi.cn/quote_{code}.html"
CFI_SECTION_URL = "https://quote.cfi.cn/quote.aspx"
DEFAULT_REPORT_DIR = "F:/VibeCoding/Codex和ClaudeCode/Memory Base/03_Skill产物/trade-agent/reports/a-share/daily"
DEFAULT_T0_FUND_MIN_AMOUNT = 50_000_000
SKIP_EXTERNAL_CONTEXT = os.environ.get("A_SHARE_SKIP_EXTERNAL_CONTEXT") == "1"
STRATEGY2_BOX_DAYS = 20
T0_FUND_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"
T0_FUND_KEYWORDS = (
    "qdii",
    "恒生",
    "港股",
    "香港",
    "h股",
    "中概",
    "中国互联网",
    "中国科技",
    "纳指",
    "纳斯达克",
    "标普",
    "道琼斯",
    "日经",
    "日本",
    "德国",
    "法国",
    "亚太",
    "印度",
    "沙特",
    "中韩",
    "韩国",
    "东南亚",
    "原油",
    "油气",
    "黄金",
    "白银",
    "豆粕",
    "商品",
    "货币",
    "现金",
    "保证金",
    "快线",
    "债",
)


_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def _prefer_ipv4_for_eastmoney(host: str, port: int, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0) -> Any:
    if "eastmoney.com" in host:
        return _ORIGINAL_GETADDRINFO(host, port, socket.AF_INET, type, proto, flags)
    return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)


socket.getaddrinfo = _prefer_ipv4_for_eastmoney


@dataclass(frozen=True)
class MarketIndex:
    symbol: str
    name: str
    price: float
    change_pct: float
    amount: float | None


@dataclass(frozen=True)
class SectionStrength:
    name: str
    up_count: int | None
    down_count: int | None
    avg_change_pct: float | None


@dataclass(frozen=True)
class CommunitySignal:
    source: str
    recent_posts: int
    total_posts: int | None
    read_count: int
    comment_count: int
    bullish_posts: int
    bearish_posts: int
    lure_posts: int
    discussion_score: float
    sentiment_score: float
    hype_risk_score: float
    sample_titles: list[str]
    lure_titles: list[str]


@dataclass(frozen=True)
class AShareCandidate:
    code: str
    name: str
    date: str
    close: float
    pct_change: float | None
    amount: float
    setup: str
    signals: list[str]
    support: float
    support_date: str
    stop: float
    target: float
    target_date: str
    reward_risk: float
    reward_risk_confidence: float
    ma5: float
    ma10: float
    ma20: float
    ma30: float
    ma60: float
    volume_ratio: float
    gain_30: float
    velocity_30: float
    gain_60: float
    buy_sell_ratio_60: float
    bullish_confidence: float
    confidence_factors: list[str]
    bearish_confidence: float
    bearish_factors: list[str]
    false_breaks: int
    industry: SectionStrength | None = None
    concepts: list[SectionStrength] | None = None
    community: CommunitySignal | None = None
    latest_note: str | None = None
    final_score: float = 0.0


@dataclass(frozen=True)
class TrendCandidate:
    code: str
    name: str
    date: str
    close: float
    pct_change: float | None
    amount: float
    gain_30: float
    velocity_30: float
    gain_60: float
    buy_sell_ratio_60: float
    ma5: float
    ma10: float
    ma20: float
    ma30: float
    ma60: float
    volume_ratio: float
    bullish_confidence: float
    confidence_factors: list[str]
    bearish_confidence: float
    bearish_factors: list[str]
    industry: SectionStrength | None = None
    concepts: list[SectionStrength] | None = None
    community: CommunitySignal | None = None
    latest_note: str | None = None
    final_score: float = 0.0


@dataclass(frozen=True)
class RangeBoundCandidate:
    code: str
    name: str
    date: str
    close: float
    pct_change: float | None
    amount: float
    range_low: float
    range_high: float
    range_days: int
    range_position: float
    range_width_pct: float
    gain_30: float
    gain_60: float
    buy_sell_ratio_60: float
    ma30: float
    ma60: float
    volume_ratio: float
    bullish_confidence: float
    confidence_factors: list[str]
    bearish_confidence: float
    bearish_factors: list[str]
    lower_edge_signals: list[str]
    industry: SectionStrength | None = None
    concepts: list[SectionStrength] | None = None
    community: CommunitySignal | None = None
    latest_note: str | None = None
    final_score: float = 0.0


@dataclass(frozen=True)
class WatchlistReview:
    code: str
    name: str
    date: str
    close: float
    pct_change: float | None
    amount: float
    status: str
    setup: str
    signals: list[str]
    support: float | None
    target: float | None
    reward_risk: float | None
    reward_risk_confidence: float | None
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma30: float | None
    ma60: float | None
    volume_ratio: float | None
    gain_30: float | None
    velocity_30: float | None
    gain_60: float | None
    buy_sell_ratio_60: float | None
    bullish_confidence: float | None
    confidence_factors: list[str]
    bearish_confidence: float | None
    bearish_factors: list[str]
    comment: str
    industry: SectionStrength | None = None
    concepts: list[SectionStrength] | None = None
    community: CommunitySignal | None = None
    latest_note: str | None = None
    final_score: float = 0.0


@dataclass(frozen=True)
class T0FundCandidate:
    candidate: AShareCandidate
    t0_reason: str


def fetch_text(url: str, params: dict[str, Any] | None = None, *, retries: int = 4, timeout: int = 20) -> str:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    full_url = f"{url}{query}"
    headers = {
        "Accept": "*/*",
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 trade-agent-a-share-daily/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="ignore")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * attempt)
                continue
            break
    raise RuntimeError(f"request failed for {url}: {last_error}")


def fetch_json(url: str, params: dict[str, Any], *, retries: int = 4, timeout: int = 20) -> Any:
    return json.loads(fetch_text(url, params, retries=retries, timeout=timeout))


def number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_price(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "n/a"
    return f"{value:+.2f}%"


def fmt_amount(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.0f}万"
    return f"{value:.0f}"


def clean_text(raw: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clean_title(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def extract_js_object(text: str, variable_name: str) -> str | None:
    marker = f"var {variable_name}="
    marker_index = text.find(marker)
    if marker_index < 0:
        return None
    start = text.find("{", marker_index)
    if start < 0:
        return None

    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def is_main_board(code: str) -> bool:
    return code.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def sina_symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def t0_fund_reason(name: str) -> str | None:
    normalized = name.lower()
    hits = [keyword for keyword in T0_FUND_KEYWORDS if keyword in normalized]
    if not hits:
        return None
    if any(keyword in normalized for keyword in ("货币", "现金", "保证金", "快线")):
        return "货币/现金类场内基金，通常支持T+0"
    if "债" in normalized:
        return "债券类场内基金，通常支持T+0"
    if any(keyword in normalized for keyword in ("原油", "油气", "黄金", "白银", "豆粕", "商品")):
        return "商品/资源类场内基金，通常支持T+0"
    if any(
        keyword in normalized
        for keyword in (
            "qdii",
            "恒生",
            "港股",
            "香港",
            "h股",
            "中概",
            "中国互联网",
            "中国科技",
            "纳指",
            "纳斯达克",
            "标普",
            "道琼斯",
            "日经",
            "日本",
            "德国",
            "法国",
            "亚太",
            "印度",
            "沙特",
            "中韩",
            "韩国",
            "东南亚",
        )
    ):
        return "跨境/QDII类场内基金，通常支持T+0"
    return "名称命中T+0场内基金关键词，需以券商交易规则复核"


def load_sina_universe(min_amount: float) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for page in range(1, 90):
        rows = fetch_json(
            SINA_QUOTE_URL,
            {
                "page": page,
                "num": 80,
                "sort": "changepercent",
                "asc": 0,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "init",
            },
            timeout=15,
        )
        if not rows:
            break
        quotes.extend(rows)
        time.sleep(0.03)

    filtered: list[dict[str, Any]] = []
    for row in quotes:
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        trade = number(row.get("trade"), 0) or 0
        amount = number(row.get("amount"), 0) or 0
        if not is_main_board(code):
            continue
        if "ST" in name.upper() or "退" in name or name.startswith(("N", "C")):
            continue
        if trade <= 0 or amount < min_amount:
            continue
        filtered.append(row)
    return filtered


def generated_main_board_symbols() -> list[str]:
    symbols: list[str] = []
    symbols.extend(f"sh{code:06d}" for code in range(600000, 606000))
    symbols.extend(f"sz{code:06d}" for code in range(1, 4000))
    return symbols


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def parse_sina_hq_quote(symbol: str, payload: str, min_amount: float) -> dict[str, Any] | None:
    parts = payload.split(",")
    if len(parts) < 32:
        return None
    code = symbol[2:]
    name = parts[0].strip()
    previous_close = number(parts[2], 0) or 0
    current = number(parts[3], 0) or 0
    amount = number(parts[9], 0) or 0
    if not name or current <= 0 or previous_close <= 0 or amount < min_amount:
        return None
    if not is_main_board(code):
        return None
    if "ST" in name.upper() or "退" in name or name.startswith(("N", "C")):
        return None
    return {
        "code": code,
        "name": name,
        "trade": current,
        "amount": amount,
        "changepercent": ((current - previous_close) / previous_close) * 100,
    }


def parse_sina_hq_quote_loose(symbol: str, payload: str, fallback_name: str | None = None) -> dict[str, Any] | None:
    parts = payload.split(",")
    if len(parts) < 32:
        return None
    code = symbol[2:]
    name = parts[0].strip() or fallback_name or code
    previous_close = number(parts[2], 0) or 0
    current = number(parts[3], 0) or 0
    amount = number(parts[9], 0) or 0
    if not name or current <= 0 or previous_close <= 0:
        return None
    return {
        "code": code,
        "name": name,
        "trade": current,
        "amount": amount,
        "changepercent": ((current - previous_close) / previous_close) * 100,
    }


def load_sina_hq_universe(min_amount: float) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for batch in chunked(generated_main_board_symbols(), 300):
        text = fetch_text(SINA_HQ_URL.format(symbols=",".join(batch)), retries=2, timeout=15)
        for symbol, payload in re.findall(r"hq_str_(\w+)=\"([^\"]*)\"", text):
            quote = parse_sina_hq_quote(symbol, payload, min_amount)
            if quote:
                quotes.append(quote)
        time.sleep(0.03)
    return quotes


def load_watchlist(path: Path | None) -> list[tuple[str, str | None]]:
    if not path or not path.exists():
        return []
    items: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.search(r"(\d{6})", line)
        if not match:
            continue
        code = match.group(1)
        if code in seen:
            continue
        tail = line[match.end() :].strip(" ,，\t")
        name = tail.split()[0] if tail else None
        items.append((code, name))
        seen.add(code)
    return items


def fetch_watchlist_quotes(items: list[tuple[str, str | None]]) -> tuple[list[dict[str, Any]], list[str]]:
    if not items:
        return [], []
    fallback_names = {code: name for code, name in items if name}
    symbols = [sina_symbol(code) for code, _ in items]
    quotes: list[dict[str, Any]] = []
    errors: list[str] = []
    for batch in chunked(symbols, 300):
        try:
            text = fetch_text(SINA_HQ_URL.format(symbols=",".join(batch)), retries=2, timeout=15)
        except Exception as exc:
            errors.append(f"自选股行情批次失败: {exc}")
            continue
        returned: set[str] = set()
        for symbol, payload in re.findall(r"hq_str_(\w+)=\"([^\"]*)\"", text):
            returned.add(symbol[2:])
            quote = parse_sina_hq_quote_loose(symbol, payload, fallback_names.get(symbol[2:]))
            if quote:
                quotes.append(quote)
        missing = [symbol[2:] for symbol in batch if symbol[2:] not in returned]
        errors.extend(f"自选股 {code}: 未取到Sina实时行情" for code in missing)
        time.sleep(0.03)
    return quotes, errors


def load_eastmoney_universe(min_amount: float) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, 20):
        payload = fetch_json(
            EASTMONEY_QUOTE_URL,
            {
                "pn": page,
                "pz": 500,
                "po": 1,
                "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80",
                "fields": "f12,f14,f2,f3,f6",
            },
            timeout=15,
        )
        data = payload.get("data") or {}
        rows = data.get("diff") or []
        total = data.get("total") or total
        if not rows:
            break
        quotes.extend(rows)
        if total and len(quotes) >= total:
            break
        time.sleep(0.05)

    filtered: list[dict[str, Any]] = []
    for row in quotes:
        code = str(row.get("f12", ""))
        name = str(row.get("f14", ""))
        trade = number(row.get("f2"), 0) or 0
        amount = number(row.get("f6"), 0) or 0
        if not is_main_board(code):
            continue
        if "ST" in name.upper() or "退" in name or name.startswith(("N", "C")):
            continue
        if trade <= 0 or amount < min_amount:
            continue
        filtered.append(
            {
                "code": code,
                "name": name,
                "trade": trade,
                "amount": amount,
                "changepercent": number(row.get("f3")),
            }
        )
    return filtered


def load_universe(min_amount: float) -> list[dict[str, Any]]:
    try:
        universe = load_sina_hq_universe(min_amount)
        if universe:
            return universe
    except Exception as exc:
        print(f"Sina HQ universe failed, falling back to Eastmoney: {exc}", file=sys.stderr, flush=True)
    try:
        universe = load_eastmoney_universe(min_amount)
        if universe:
            return universe
    except Exception as exc:
        print(f"Eastmoney universe failed, falling back to Sina: {exc}", file=sys.stderr, flush=True)
    return load_sina_universe(min_amount)


def load_t0_fund_universe(min_amount: float) -> list[dict[str, Any]]:
    payload = fetch_json(
        EASTMONEY_QUOTE_URL,
        {
            "pn": 1,
            "pz": 5000,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f6",
            "fs": T0_FUND_FS,
            "fields": "f2,f3,f4,f5,f6,f8,f10,f12,f13,f14,f18,f20,f21",
        },
        timeout=20,
    )
    quotes: list[dict[str, Any]] = []
    for row in (payload.get("data") or {}).get("diff") or []:
        code = str(row.get("f12", "")).zfill(6)
        name = str(row.get("f14", "")).strip()
        trade = number(row.get("f2"), 0) or 0
        previous_close = number(row.get("f18"), 0) or 0
        amount = number(row.get("f6"), 0) or 0
        reason = t0_fund_reason(name)
        if not code or not name or trade <= 0 or previous_close <= 0 or amount < min_amount or not reason:
            continue
        quotes.append(
            {
                "code": code,
                "name": name,
                "trade": trade,
                "amount": amount,
                "changepercent": number(row.get("f3"), 0) or 0,
                "t0_reason": reason,
            }
        )
    return quotes


def fetch_daily_bars_sina(code: str) -> list[Bar]:
    rows = fetch_json(
        SINA_KLINE_URL,
        {"symbol": sina_symbol(code), "scale": "240", "ma": "no", "datalen": "650"},
        retries=3,
        timeout=15,
    )
    bars: list[Bar] = []
    for row in rows[-620:]:
        bars.append(
            Bar(
                date=str(row["day"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0),
            )
        )
    if len(bars) < 130:
        raise RuntimeError("not enough daily history")
    return bars


def fetch_daily_bars_tencent(code: str) -> list[Bar]:
    symbol = sina_symbol(code)
    payload = fetch_json(
        TENCENT_KLINE_URL,
        {"param": f"{symbol},day,,,650,qfq"},
        retries=3,
        timeout=15,
    )
    data = (payload.get("data") or {}).get(symbol) or {}
    rows = data.get("qfqday") or data.get("day") or []
    bars: list[Bar] = []
    for row in rows[-620:]:
        if len(row) < 6:
            continue
        bars.append(
            Bar(
                date=str(row[0]),
                open=float(row[1]),
                close=float(row[2]),
                high=float(row[3]),
                low=float(row[4]),
                volume=float(row[5]),
            )
        )
    if len(bars) < 130:
        raise RuntimeError("not enough daily history")
    return bars


def fetch_daily_bars(code: str) -> list[Bar]:
    try:
        return fetch_daily_bars_tencent(code)
    except Exception as tencent_error:
        try:
            return fetch_daily_bars_sina(code)
        except Exception as sina_error:
            raise RuntimeError(f"Tencent: {tencent_error}; Sina: {sina_error}") from sina_error


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def bullish_engulfing(bars: list[Bar]) -> bool:
    if len(bars) < 2:
        return False
    previous, current = bars[-2], bars[-1]
    return (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
        and (current.close - current.open) / current.open > 0.01
    )


def morning_star(bars: list[Bar]) -> bool:
    if len(bars) < 3:
        return False
    first, second, third = bars[-3], bars[-2], bars[-1]
    first_body = abs(first.close - first.open)
    first_range = max(first.high - first.low, 1e-9)
    second_body = abs(second.close - second.open)
    second_range = max(second.high - second.low, 1e-9)
    return (
        first.close < first.open
        and first_body / first_range > 0.45
        and second_body / second_range < 0.35
        and third.close > third.open
        and third.close >= first.open - first_body * 0.5
        and third.close > second.high
    )


def strong_breakout_bar(bars: list[Bar], resistance: float) -> bool:
    current = bars[-1]
    body = abs(current.close - current.open)
    trading_range = max(current.high - current.low, 1e-9)
    volume_base = avg([bar.volume for bar in bars[-21:-1]])
    return (
        current.close > current.open
        and body / trading_range > 0.45
        and current.close > resistance * 1.003
        and volume_base > 0
        and current.volume > volume_base * 1.15
    )


def swing_highs(bars: list[Bar], left: int = 3, right: int = 3) -> list[tuple[str, float]]:
    highs: list[tuple[str, float]] = []
    for index in range(left, len(bars) - right):
        high = bars[index].high
        if all(bars[pos].high <= high for pos in range(index - left, index + right + 1) if pos != index):
            highs.append((bars[index].date, high))
    return highs


def indexed_swing_highs(bars: list[Bar], left: int = 3, right: int = 3) -> list[tuple[int, str, float]]:
    highs: list[tuple[int, str, float]] = []
    for index in range(left, len(bars) - right):
        high = bars[index].high
        if all(bars[pos].high <= high for pos in range(index - left, index + right + 1) if pos != index):
            highs.append((index, bars[index].date, high))
    return highs


def indexed_swing_lows(bars: list[Bar], left: int = 3, right: int = 3) -> list[tuple[int, str, float]]:
    lows: list[tuple[int, str, float]] = []
    for index in range(left, len(bars) - right):
        low = bars[index].low
        if all(bars[pos].low >= low for pos in range(index - left, index + right + 1) if pos != index):
            lows.append((index, bars[index].date, low))
    return lows


def false_breakout_count(bars: list[Bar], level: float) -> int:
    return sum(1 for bar in bars[-25:-1] if bar.high > level * 1.002 and bar.close < level)


def recent_breakout_retest(
    bars: list[Bar],
    lookback_days: int = 12,
) -> tuple[bool, float | None, str | None]:
    current = bars[-1]
    start = max(70, len(bars) - lookback_days)
    for index in range(start, len(bars) - 1):
        level = max(bar.high for bar in bars[index - 30 : index])
        breakout_day = bars[index]
        if breakout_day.close <= level * 1.003:
            continue
        post_breakout = bars[index + 1 :]
        if (
            current.low <= level * 1.025
            and current.close > level
            and current.close <= level * 1.065
            and min(bar.low for bar in post_breakout) >= level * 0.985
        ):
            return True, level, breakout_day.date
    return False, None, None


def retest_hold_signal(bar: Bar, level: float) -> bool:
    return (
        bar.low <= level * 1.025
        and bar.close > level
        and (bar.close > bar.open or bullish_pinbar(bar))
    )


def recent_ema20_second_wave(
    bars: list[Bar],
    closes: list[float],
    volumes: list[float],
    lookback_days: int = 2,
) -> tuple[bool, float | None, str | None, float | None, list[str]]:
    if len(bars) < 95:
        return False, None, None, None, []

    current = bars[-1]
    ema20_now = ema_series(closes, 20)[-1]
    ma60_now = sma(closes, 60)
    ema20_before = ema_series(closes[:-5], 20)[-1] if len(closes) > 65 else None
    if ema20_now is None or ma60_now is None or ema20_before is None:
        return False, None, None, None, []

    volume_base = max(avg(volumes[-21:-1]), 1)
    recent_high = max((bar.high for bar in bars[-6:-1]), default=current.high)
    launch_bar = (
        current.close > current.open
        and current.close > ema20_now * 1.02
        and (current.volume >= volume_base * 1.1 or current.close >= recent_high * 0.995)
    )
    if not launch_bar:
        return False, None, None, None, []

    for index in range(max(65, len(bars) - lookback_days), len(bars)):
        retest_bar = bars[index]
        ema20_at_retest = ema_series(closes[: index + 1], 20)[-1]
        ma60_at_retest = sma(closes[: index + 1], 60)
        if ema20_at_retest is None or ma60_at_retest is None:
            continue

        pre_window = bars[max(0, index - 90) : index]
        if len(pre_window) < 45:
            continue

        prior_low = min(bar.low for bar in pre_window)
        prior_high = max(bar.high for bar in pre_window)
        prior_runup = pct_change(prior_high, prior_low)
        pullback_pct = ((prior_high - retest_bar.low) / max(prior_high, 1e-9)) * 100
        touched_ema20 = retest_bar.low <= ema20_at_retest * 1.04
        held_ema20 = retest_bar.close >= ema20_at_retest * 0.985
        post_retest_holds = True
        for offset in range(index, len(bars)):
            ema20_at_offset = ema_series(closes[: offset + 1], 20)[-1]
            if ema20_at_offset is not None and bars[offset].close < ema20_at_offset * 0.985:
                post_retest_holds = False
                break

        if not (
            prior_runup >= 40
            and 12 <= pullback_pct <= 55
            and touched_ema20
            and held_ema20
            and post_retest_holds
            and ema20_at_retest >= ma60_at_retest
            and ema20_now >= ema20_before * 0.99
            and current.close <= prior_high * 1.35
        ):
            continue

        measured_target = current.close + max(current.close * 0.06, (prior_high - retest_bar.low) * 0.5)
        factors = [
            f"前段涨幅{prior_runup:.0f}%",
            f"前高回撤{pullback_pct:.0f}%",
            "回踩EMA20不破",
        ]
        if current.volume >= volume_base * 1.1:
            factors.append(f"二波放量启动({current.volume / volume_base:.1f}x)")
        else:
            factors.append("二波右侧启动")
        return True, ema20_at_retest, retest_bar.date, measured_target, factors

    return False, None, None, None, []


def detect_ema20_breakout(
    bars: list[Bar],
    closes: list[float],
    volumes: list[float],
) -> tuple[bool, float | None, str | None, list[str]]:
    if len(bars) < 35:
        return False, None, None, []
    current = bars[-1]
    previous = bars[-2]
    ema20 = ema_series(closes, 20)[-1]
    if ema20 is None:
        return False, None, None, []
    volume_base = max(avg(volumes[-21:-1]), 1)
    if not (
        current.close > ema20 * 1.005
        and previous.close <= ema20 * 1.005
        and current.volume >= volume_base * 1.15
    ):
        return False, None, None, []
    return True, ema20, current.date, ["放量突破EMA20"]


def detect_descending_channel_breakout(
    bars: list[Bar],
    closes: list[float],
    volumes: list[float],
) -> tuple[bool, float | None, str | None, list[str]]:
    """检测趋势早期信号D：涨停大阳线突破持续20根K线以上的下降通道，且收盘在EMA20上方。

    条件：
    1. 当日涨幅 >= 9.5%（A股主板涨停板附近），且为实体大阳线（收盘接近最高价）
    2. 最近60根K线（不含当日）中存在至少3个持续下降的摆荡高点，构成下降通道
    3. 通道至少持续20根K线
    4. 涨停K线最高价突破通道上轨的延伸线
    5. 收盘价在EMA20上方
    """
    if len(bars) < 65:
        return False, None, None, []

    current = bars[-1]
    previous = bars[-2]

    # 1. 涨停大阳线检查（A股主板 ±10% 涨跌停）
    # B2 fix: 必须先确认是阳线（close > open），
    # abs(close-open) 会让阴线也通过"实体大阳线"门槛。
    if current.close <= current.open:
        return False, None, None, []
    pct_chg = (current.close - previous.close) / max(previous.close, 1e-9) * 100
    if pct_chg < 9.5:
        return False, None, None, []

    # 确认是实体大阳线（收盘在高位，上影线短）
    trading_range = max(current.high - current.low, 1e-9)
    body = current.close - current.open  # 已经保证 close > open
    upper_shadow = current.high - current.close  # close >= open，high 之上的才是上影
    if body / trading_range < 0.55:
        return False, None, None, []
    if upper_shadow > trading_range * 0.25:
        return False, None, None, []

    # 2. 下降通道检测：在最近60根K线（不含今日）中找摆荡高点
    window_size = min(60, len(bars) - 1)
    window = bars[-window_size - 1 : -1]

    swing_highs = indexed_swing_highs(window, left=3, right=3)
    if len(swing_highs) < 3:
        return False, None, None, []

    # 取最近3-4个摆荡高点，检查是否持续下降
    recent_highs = swing_highs[-4:] if len(swing_highs) >= 4 else swing_highs[-3:]
    high_values = [h[2] for h in recent_highs]

    descending = all(
        high_values[i] > high_values[i + 1] * 1.003 for i in range(len(high_values) - 1)
    )
    if not descending:
        return False, None, None, []

    # 通道至少持续20根K线
    channel_start_idx = recent_highs[0][0]
    channel_end_idx = recent_highs[-1][0]
    channel_duration = channel_end_idx - channel_start_idx
    if channel_duration < 20:
        return False, None, None, []

    # 3. 计算通道上轨在当前bar的延伸位置，确认突破
    h1 = recent_highs[-2]
    h2 = recent_highs[-1]
    h1_idx, h1_val = h1[0], h1[2]
    h2_idx, h2_val = h2[0], h2[2]

    h_gap = max(h2_idx - h1_idx, 1)
    slope = (h2_val - h1_val) / h_gap

    # B3 fix: 拒绝异常陡峭的负斜率（上轨失真 → 任意涨停都能"突破"）。
    # slope 已是每根 K 线的价格变化，不应再除以 h_gap。
    # 合理区间：下降通道上轨每 K 线跌幅不宜超过 0.8%。
    if slope < -h1_val * 0.008:
        return False, None, None, []

    current_idx_in_window = len(window)
    channel_top = h2_val + slope * (current_idx_in_window - h2_idx)

    # B3 fix: 校验今日是首次突破。h2 后到昨日的任一 K 线如果曾越过
    # 当日延伸上轨，说明突破早已发生；即使昨日回到轨内也不应重复标记。
    for bar_idx in range(h2_idx + 1, current_idx_in_window):
        projected_top = h2_val + slope * (bar_idx - h2_idx)
        if window[bar_idx].high > projected_top:
            return False, None, None, []

    if current.high <= channel_top:
        return False, None, None, []

    # 4. 收盘在EMA20上方
    ema20 = ema_series(closes, 20)[-1]
    if ema20 is None:
        return False, None, None, []
    if current.close <= ema20:
        return False, None, None, []

    # 5. 成交量确认
    volume_base = max(avg(volumes[-21:-1]), 1)
    vol_ratio = current.volume / volume_base

    # B1 fix: 信号键必须使用稳定字符串（与 price_action_rank_score 权重表精确匹配）。
    # 动态数值（涨幅%、量比）记录在因子中供报告展示，不参与信号权重查找。
    factors = [
        "涨停突破下降通道",
        f"通道持续{channel_duration}根K线",
        "收盘站上EMA20",
    ]
    if vol_ratio >= 1.5:
        factors.append("放量涨停")
        factors.append(f"量比{vol_ratio:.1f}x")
    elif vol_ratio >= 1.2:
        factors.append("放量配合")
        factors.append(f"量比{vol_ratio:.1f}x")

    # 涨幅信息单独记录（供报告展示，不参与权重匹配）
    factors.append(f"当日涨幅+{pct_chg:.1f}%")

    return True, channel_top, current.date, factors


def detect_50pct_retracement(
    bars: list[Bar],
    closes: list[float],
    volumes: list[float],
) -> tuple[bool, float | None, str | None, list[str]]:
    """检测回踩 50% 回调位 — 策略一优先信号。

    从最近一段显著上升趋势中识别波段低点和高点，
    计算 50% 回撤位，判断当前价格是否回踩该位置并出现多头支撑信号。
    """
    if len(bars) < 90:
        return False, None, None, []

    current = bars[-1]
    window = bars[-90:]

    highs = indexed_swing_highs(window, left=3, right=3)
    lows = indexed_swing_lows(window, left=3, right=3)

    if len(highs) < 1 or len(lows) < 1:
        return False, None, None, []

    # 最近 90 根 K 线内的最高摆荡高点
    recent_high = max(highs, key=lambda h: h[2])
    high_idx, high_date, high_price = recent_high

    # 该高点之前至少 10 根 K 线的波段低点
    prior_lows = [l for l in lows if l[0] < high_idx - 10]
    if not prior_lows:
        return False, None, None, []

    prior_low = min(prior_lows, key=lambda l: l[2])
    low_idx, low_date, low_price = prior_low

    # 上升波段至少 15% 涨幅才有意义的回调
    runup_pct = pct_change(high_price, low_price)
    if runup_pct < 15:
        return False, None, None, []

    # 50% 回撤位
    retrace_50 = low_price + (high_price - low_price) * 0.5

    # 当前价格需在 50% 位 ±3.5% 范围内
    deviation = (current.close - retrace_50) / max(retrace_50, 1e-9)
    if abs(deviation) > 0.035:
        return False, None, None, []

    # 回调幅度在上升波段的 30%-70% 之间
    pullback_ratio = (high_price - current.close) / max(high_price - low_price, 1e-9)
    if pullback_ratio < 0.30 or pullback_ratio > 0.70:
        return False, None, None, []

    # 多头支撑信号
    factors: list[str] = []

    if current.close > current.open:
        if current.close > current.open * 1.02:
            factors.append("50%位实体阳线")
        else:
            factors.append("50%位收阳")
    elif bullish_pinbar(current):
        factors.append("50%位Pinbar")
    elif higher_low(bars[-5:], closes[-5:]):
        factors.append("50%位higher_low")
    else:
        return False, None, None, []

    # 成交量确认
    volume_base = max(avg(volumes[-21:-1]), 1)
    if current.volume >= volume_base * 0.85:
        factors.append("50%位放量")

    # 近 5 日最低价不能显著跌破 50% 位
    recent_5_lows = [bar.low for bar in bars[-5:]]
    if min(recent_5_lows) < retrace_50 * 0.965:
        return False, None, None, []

    # EMA20 位置辅助判断
    ema20 = ema_series(closes, 20)[-1]
    if ema20 is not None:
        if current.close > ema20:
            factors.append("站上EMA20")
        elif current.close > ema20 * 0.97:
            factors.append("靠近EMA20")

    factors.insert(0, f"50%回调{retrace_50:.2f}")
    return True, retrace_50, current.date, factors


def candle_close_position(bar: Bar) -> float:
    trading_range = max(bar.high - bar.low, 1e-9)
    return (bar.close - bar.low) / trading_range


def candle_body_ratio(bar: Bar) -> float:
    return abs(bar.close - bar.open) / max(bar.high - bar.low, 1e-9)


def brooks_context_adjustment(
    bars: list[Bar],
    closes: list[float],
    ma30: float,
    ma60: float,
) -> tuple[float, list[str], float, list[str]]:
    if len(bars) < 90:
        return 0.0, [], 0.0, []

    recent = bars[-120:] if len(bars) >= 120 else bars[-60:]
    recent_20 = bars[-20:]
    bullish_bonus = 0.0
    bullish_factors: list[str] = []
    bearish_bonus = 0.0
    bearish_factors: list[str] = []

    highs = indexed_swing_highs(recent, left=3, right=3)
    lows = indexed_swing_lows(recent, left=3, right=3)
    higher_highs = len(highs) >= 2 and highs[-1][2] > highs[-2][2] * 1.01
    higher_lows = len(lows) >= 2 and lows[-1][2] > lows[-2][2] * 1.01
    ma30_prior = sma(closes[:-10], 30)
    range_high = max(bar.high for bar in recent)
    range_low = min(bar.low for bar in recent)
    range_pct = (range_high - range_low) / max(range_low, 1e-9)
    upper_position = (bars[-1].close - range_low) / max(range_high - range_low, 1e-9)
    overlap_count = 0
    for previous, current in zip(recent_20, recent_20[1:]):
        if min(previous.high, current.high) >= max(previous.low, current.low):
            overlap_count += 1
    overlap_ratio = overlap_count / max(len(recent_20) - 1, 1)

    if ma30 > ma60 and ma30_prior is not None and ma30 > ma30_prior and higher_highs and higher_lows:
        bullish_bonus += 20.0
        bullish_factors.append("强趋势背景")

    # 长期趋势检测（200 根 K 线）
    long_term_highs = indexed_swing_highs(recent, left=4, right=4)
    long_term_lows = indexed_swing_lows(recent, left=4, right=4)
    lt_higher_highs = len(long_term_highs) >= 3 and sum(1 for i in range(1, len(long_term_highs)) if long_term_highs[i][2] > long_term_highs[i-1][2] * 1.005) >= 2
    lt_higher_lows = len(long_term_lows) >= 3 and sum(1 for i in range(1, len(long_term_lows)) if long_term_lows[i][2] > long_term_lows[i-1][2] * 1.005) >= 2
    if lt_higher_highs and lt_higher_lows:
        bullish_bonus += 8.0
        bullish_factors.append(f"长期趋势向上({len(long_term_highs)}高{len(long_term_lows)}低)")
    elif lt_higher_highs or lt_higher_lows:
        bullish_bonus += 4.0
        bullish_factors.append("长期趋势偏多")

    if overlap_ratio >= 0.72 and range_pct <= 0.32:
        bearish_bonus += 8.0
        bearish_factors.append("重叠K较多/交易区间倾向")
    if overlap_ratio >= 0.62 and upper_position >= 0.78:
        bearish_bonus += 5.0
        bearish_factors.append("交易区间上沿首次突破需跟随确认")
    if range_pct >= 0.55 and bars[-1].close > ma30:
        bearish_bonus += 4.0
        bearish_factors.append("波动区间较宽，结构止损要求更严格")

    return bullish_bonus, bullish_factors, bearish_bonus, bearish_factors


def three_push_wedge(bars: list[Bar], closes: list[float]) -> tuple[bool, list[str]]:
    """Al Brooks 三推楔形牛旗形态检测。

    条件：
    1. 最近 40-80 根内有 3 个依次降低的摆荡低点
    2. 低点之间跌幅递减（第三推动能最弱）
    3. 第三个低点后出现看涨反转信号
    """
    if len(bars) < 40:
        return False, []
    window = bars[-80:] if len(bars) >= 80 else bars
    swing_lows = indexed_swing_lows(window, left=2, right=2)
    if len(swing_lows) < 3:
        return False, []

    # 取最近 3 个摆荡低点
    lows = swing_lows[-3:]
    push1_val = lows[0][2]
    push2_val = lows[1][2]
    push3_val = lows[2][2]
    if not (push1_val > push2_val > push3_val):
        return False, []

    drop1 = (push1_val - push2_val) / push1_val * 100
    drop2 = (push2_val - push3_val) / push2_val * 100
    if not (drop1 > drop2 * 1.2):
        return False, []

    # 第三推后需要看涨反转信号
    current = bars[-1]
    has_reversal = (
        bullish_pinbar(current)
        or bullish_engulfing(bars)
        or morning_star(bars)
    )
    if not has_reversal:
        return False, []

    return True, [f"三推楔形牛旗:push递减{drop1:.1f}%/{drop2:.1f}%"]


def breakout_index_by_date(bars: list[Bar], breakout_date: str | None) -> int | None:
    if not breakout_date:
        return None
    for index, bar in enumerate(bars):
        if bar.date == breakout_date:
            return index
    return None


def brooks_breakout_confirmation(
    bars: list[Bar],
    level: float,
    breakout_date: str | None,
    setup: str,
) -> tuple[float, list[str], float, list[str]]:
    if setup in ("回踩50%", "二波回踩EMA20", "突破EMA20", "下降通道突破"):
        factor_map = {
            "回踩50%": "50%回调确认",
            "二波回踩EMA20": "二波右侧确认",
            "突破EMA20": "放量突破EMA20确认",
            "下降通道突破": "下降通道涨停突破确认",
        }
        return 6.0, [factor_map.get(setup, "形态确认")], 0.0, []

    breakout_index = breakout_index_by_date(bars, breakout_date)
    if breakout_index is None:
        return 0.0, [], 0.0, []

    breakout_bar = bars[breakout_index]
    post_breakout = bars[breakout_index + 1 :]
    bullish_bonus = 0.0
    bullish_factors: list[str] = []
    bearish_bonus = 0.0
    bearish_factors: list[str] = []

    if candle_close_position(breakout_bar) >= 0.65 and candle_body_ratio(breakout_bar) >= 0.45:
        bullish_bonus += 5.0
        bullish_factors.append("突破K收盘强")
    else:
        bearish_bonus += 5.0
        bearish_factors.append("突破K实体/收盘偏弱")

    hold_days = sum(1 for bar in post_breakout if bar.close > level)
    if len(post_breakout) >= 2 and hold_days >= min(3, len(post_breakout)):
        bullish_bonus += 8.0
        bullish_factors.append(f"突破后站稳{hold_days}日")
    if any(bar.close < level for bar in post_breakout[-3:]):
        bearish_bonus += 10.0
        bearish_factors.append("突破后跌回压力位")

    retest_bars = [bar for bar in post_breakout if bar.low <= level * 1.035 and bar.close > level * 0.995]
    if len(retest_bars) >= 2 and retest_hold_signal(bars[-1], level):
        bullish_bonus += 10.0
        bullish_factors.append("二次回踩不破")

    return bullish_bonus, bullish_factors, bearish_bonus, bearish_factors


def measured_move_target_from_breakout(
    bars: list[Bar],
    level: float,
    breakout_date: str | None,
    current_close: float,
) -> tuple[float | None, str | None]:
    breakout_index = breakout_index_by_date(bars, breakout_date)
    if breakout_index is None or breakout_index < 30:
        return None, None
    base_window = bars[breakout_index - 30 : breakout_index]
    if not base_window:
        return None, None
    base_low = min(bar.low for bar in base_window)
    target = level + max(level - base_low, current_close * 0.04)
    if target <= current_close * 1.025:
        return None, None
    return target, "箱体量度目标"


def structure_stop_for_setup(
    bars: list[Bar],
    current: Bar,
    level: float,
    setup: str,
) -> float | None:
    if setup in ("回踩50%", "二波回踩EMA20"):
        structural_low = min(bar.low for bar in bars[-5:])
    else:
        nearby_tests = [bar.low for bar in bars[-6:] if bar.low <= level * 1.04]
        structural_low = min(nearby_tests) if nearby_tests else min(bar.low for bar in bars[-3:])
    stop = structural_low * 0.995
    if stop >= current.close:
        return None
    if stop < current.close * 0.95:
        return None
    return stop


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / previous) * 100


def buy_sell_volume_ratio(bars: list[Bar], days: int = 60) -> float:
    buy_volume = 0.0
    sell_volume = 0.0
    for bar in bars[-days:]:
        if bar.close > bar.open:
            buy_volume += bar.volume
        elif bar.close < bar.open:
            sell_volume += bar.volume
        else:
            buy_volume += bar.volume * 0.5
            sell_volume += bar.volume * 0.5
    if sell_volume == 0:
        return math.inf if buy_volume > 0 else 0.0
    return buy_volume / sell_volume


def reward_risk_confidence(reward_risk: float | None) -> float | None:
    if reward_risk is None or not math.isfinite(reward_risk) or reward_risk <= 0:
        return None
    if reward_risk < 1.0:
        return max(5.0, min(45.0, reward_risk * 45.0))
    if reward_risk < 1.5:
        return 50.0 + (reward_risk - 1.0) / 0.5 * 15.0
    if reward_risk < 2.0:
        return 65.0 + (reward_risk - 1.5) / 0.5 * 13.0
    if reward_risk < 3.0:
        return 78.0 + (reward_risk - 2.0) * 12.0
    return min(98.0, 90.0 + min(reward_risk - 3.0, 4.0) * 2.0)


def fmt_confidence(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "n/a"


def ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    multiplier = 2 / (period + 1)
    ema_value = sum(values[:period]) / period
    result[period - 1] = ema_value
    for index in range(period, len(values)):
        ema_value = (values[index] - ema_value) * multiplier + ema_value
        result[index] = ema_value
    return result


def macd_dif_series(values: list[float]) -> list[float | None]:
    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)
    result: list[float | None] = []
    for fast, slow in zip(ema12, ema26):
        result.append(None if fast is None or slow is None else fast - slow)
    return result


def bullish_pinbar(bar: Bar) -> bool:
    trading_range = max(bar.high - bar.low, 1e-9)
    body = abs(bar.close - bar.open)
    upper_shadow = bar.high - max(bar.open, bar.close)
    lower_shadow = min(bar.open, bar.close) - bar.low
    return (
        lower_shadow >= max(body * 2, trading_range * 0.35)
        and lower_shadow >= upper_shadow * 1.5
        and bar.close >= bar.low + trading_range * 0.6
    )


def confidence_from_ema20_signal(bars: list[Bar], closes: list[float], volumes: list[float]) -> tuple[float, list[str]]:
    if len(bars) < 32:
        return 0.0, []
    current = bars[-1]
    previous = bars[-2]
    ema20 = ema_series(closes, 20)[-1]
    previous_ema20 = ema_series(closes[:-1], 20)[-1]
    volume_base = avg(volumes[-21:-1])
    bonus = 0.0
    factors: list[str] = []

    if (
        ema20 is not None
        and previous_ema20 is not None
        and volume_base > 0
        and previous.close <= previous_ema20
        and current.close > ema20
        and current.volume >= volume_base * 1.2
    ):
        bonus += 12
        factors.append("放量上穿EMA20")

    if ema20 is not None and current.low <= ema20 * 1.015 and current.close >= ema20 and bullish_pinbar(current):
        bonus += 12
        factors.append("回踩EMA20不破+Pinbar")

    return bonus, factors


def ema20_support_strength(bars: list[Bar], closes: list[float]) -> tuple[int, int, float, list[str]]:
    """回溯最近 120 根 K 线，统计 EMA20 支撑有效性。

    返回 (支撑次数, 跌破次数, 支撑评分 0-10, 因子字符串列表)。
    评分 ≥ 3 表示 EMA20 作为趋势支撑较可靠。
    """
    if len(bars) < 70:
        return 0, 0, 0.0, []
    supports = 0
    breakdowns = 0
    for i in range(max(0, len(bars) - 120), len(bars) - 1):
        ema20_i = ema_series(closes[: i + 1], 20)[-1]
        if ema20_i is None:
            continue
        bar = bars[i]
        if bar.low <= ema20_i * 1.02 and bar.close > ema20_i * 1.005:
            supports += 1
        elif bar.close < ema20_i * 0.985:
            breakdowns += 1
    if supports + breakdowns == 0:
        return 0, 0, 0.0, []
    ratio = supports / max(supports + breakdowns, 1)
    score = min(10.0, supports * 3.5 * ratio)
    factors: list[str] = []
    if score >= 5.0:
        factors.append(f"EMA20趋势支撑强({supports}/{supports+breakdowns})")
    elif score >= 3.0:
        factors.append(f"EMA20支撑确认({supports}/{supports+breakdowns})")
    return supports, breakdowns, score, factors


def confidence_from_volume_breakout(
    bars: list[Bar],
    resistance: float,
    volume_base: float,
    *,
    ratio: float = 1.2,
) -> tuple[float, list[str]]:
    current = bars[-1]
    if volume_base <= 0:
        return 0.0, []
    if current.close > resistance * 1.003 and current.volume >= volume_base * ratio:
        volume_ratio = current.volume / volume_base
        return min(18.0, 10.0 + min(volume_ratio - ratio, 2.0) * 4), [f"放量突破({volume_ratio:.1f}x)"]
    return 0.0, []


def neckline_volume_confirmed(current: Bar, neckline: float, volume_base: float) -> bool:
    return volume_base > 0 and current.close > neckline * 1.003 and current.volume >= volume_base * 1.2


def confidence_from_reversal_structures(bars: list[Bar], closes: list[float]) -> tuple[float, list[str], float, list[str]]:
    recent_bars = bars[-90:]
    volume_base = avg([bar.volume for bar in bars[-21:-1]])
    current = recent_bars[-1]
    bullish_bonus = 0.0
    bullish_factors: list[str] = []
    bearish_bonus = 0.0
    bearish_factors: list[str] = []

    highs = indexed_swing_highs(recent_bars)
    lows = indexed_swing_lows(recent_bars)

    if len(lows) >= 2:
        left_low, right_low = lows[-2], lows[-1]
        similar_lows = abs(right_low[2] - left_low[2]) / max(left_low[2], 1e-9) <= 0.03
        enough_gap = right_low[0] - left_low[0] >= 8
        neckline = max((bar.high for bar in recent_bars[left_low[0] : right_low[0] + 1]), default=current.close)
        right_side_confirm = current.close > neckline * 0.985 or current.close > right_low[2] * 1.08
        if similar_lows and enough_gap and right_side_confirm:
            bonus = 12
            factor = "双底"
            if neckline_volume_confirmed(current, neckline, volume_base):
                bonus += 6
                factor = "双底放量突破颈线"
            bullish_bonus += bonus
            bullish_factors.append(factor)

    if len(lows) >= 3:
        last_three_lows = lows[-3:]
        average_low = sum(low[2] for low in last_three_lows) / 3
        low_cluster = all(abs(low[2] - average_low) / max(average_low, 1e-9) <= 0.03 for low in last_three_lows)
        enough_spread = last_three_lows[-1][0] - last_three_lows[0][0] >= 16
        neckline = max((bar.high for bar in recent_bars[last_three_lows[0][0] : last_three_lows[-1][0] + 1]), default=current.close)
        if low_cluster and enough_spread and current.close > neckline * 0.985:
            bonus = 16
            factor = "三重底"
            if neckline_volume_confirmed(current, neckline, volume_base):
                bonus += 6
                factor = "三重底放量突破颈线"
            bullish_bonus += bonus
            bullish_factors.append(factor)

    if len(lows) >= 3 and len(highs) >= 2:
        left_shoulder, head, right_shoulder = lows[-3], lows[-2], lows[-1]
        shoulders_close = abs(right_shoulder[2] - left_shoulder[2]) / max(left_shoulder[2], 1e-9) <= 0.08
        head_lower = head[2] < min(left_shoulder[2], right_shoulder[2]) * 0.94
        right_shoulder_higher = right_shoulder[2] >= left_shoulder[2] * 0.98
        neckline_highs = [high[2] for high in highs if left_shoulder[0] < high[0] < right_shoulder[0]]
        neckline = max(neckline_highs) if neckline_highs else max(bar.high for bar in recent_bars[head[0] : right_shoulder[0] + 1])
        if shoulders_close and head_lower and right_shoulder_higher and current.close > neckline * 0.985:
            bonus = 18
            factor = "头肩底"
            if neckline_volume_confirmed(current, neckline, volume_base):
                bonus += 6
                factor = "头肩底放量突破颈线"
            bullish_bonus += bonus
            bullish_factors.append(factor)

    if len(recent_bars) >= 55:
        left_segment = recent_bars[-55:-35]
        middle_segment = recent_bars[-35:-18]
        right_segment = recent_bars[-18:]
        left_avg = avg([bar.close for bar in left_segment])
        middle_min = min(bar.low for bar in middle_segment)
        right_avg = avg([bar.close for bar in right_segment])
        recent_low = min(bar.low for bar in recent_bars[-45:])
        no_new_low_recently = min(bar.low for bar in recent_bars[-20:]) >= recent_low * 0.99
        slow_turn = left_avg > middle_min * 1.08 and right_avg > middle_min * 1.08 and right_avg > left_avg * 0.92
        if slow_turn and no_new_low_recently and current.close > sma(closes, 20) * 0.99:
            bullish_bonus += 10
            bullish_factors.append("圆弧底")

    if len(recent_bars) >= 25:
        v_window = recent_bars[-35:]
        low_index, _, low_value = min(indexed_swing_lows(v_window) or [(0, current.date, current.low)], key=lambda item: item[2])
        absolute_low_index = len(recent_bars) - len(v_window) + low_index
        prior_high = max((bar.high for bar in recent_bars[: max(1, absolute_low_index)]), default=current.high)
        rebound = (current.close - low_value) / max(low_value, 1e-9)
        prior_drop = (prior_high - low_value) / max(prior_high, 1e-9)
        right_confirm = current.close > sma(closes, 10) and current.close > current.open
        if prior_drop >= 0.18 and rebound >= prior_drop * 0.5 and right_confirm:
            bullish_bonus += 8
            bullish_factors.append("V底右侧确认")

    if len(highs) >= 2:
        left, right = highs[-2], highs[-1]
        similar_highs = abs(right[2] - left[2]) / max(left[2], 1e-9) <= 0.035
        enough_gap = right[0] - left[0] >= 8
        valley = min((bar.low for bar in recent_bars[left[0] : right[0] + 1]), default=right[2])
        valley_drop = (min(left[2], right[2]) - valley) / max(min(left[2], right[2]), 1e-9)
        current = recent_bars[-1]
        if similar_highs and enough_gap and valley_drop >= 0.06 and current.close < right[2] * 0.97:
            bearish_bonus += 14
            bearish_factors.append("M字顶")

    near_highs: list[tuple[int, str, float]] = []
    for high in highs[-8:]:
        if not near_highs:
            near_highs.append(high)
            continue
        reference = sum(item[2] for item in near_highs) / len(near_highs)
        if abs(high[2] - reference) / max(reference, 1e-9) <= 0.03:
            near_highs.append(high)
    if len(near_highs) >= 3 and recent_bars[-1].close < max(item[2] for item in near_highs) * 0.985:
        bearish_bonus += 12
        bearish_factors.append("多重顶")

    return bullish_bonus, bullish_factors, bearish_bonus, bearish_factors


def confidence_from_price_action(setup: str, signals: list[str]) -> tuple[float, list[str]]:
    # K线形态信号仍检测并显示在 signals 列表中，但不再参与置信度打分。
    # 仅按 setup 类型给固定置信度加分。
    setup_bonuses = {
        "回踩50%": 20.0,
        "突破后回踩": 18.0,
        "二波回踩EMA20": 18.0,
        "突破EMA20": 18.0,
        "下降通道突破": 16.0,
    }
    bonus = setup_bonuses.get(setup, 16.0)
    factors = [f"裸Ksetup:{setup}"]
    return min(bonus, 48.0), factors


def price_action_rank_score(candidate: AShareCandidate) -> float:
    setup_scores = {
        "回踩50%": 32.0,
        "突破后回踩": 30.0,
        "二波回踩EMA20": 29.0,
        "突破EMA20": 27.0,
        "下降通道突破": 25.0,
    }
    setup_score = setup_scores.get(candidate.setup, 26.0)
    signal_weights = {
        "50%位实体阳线": 24.0,
        "放量实体突破": 24.0,
        "50%位Pinbar": 22.0,
        "回踩不破": 22.0,
        "EMA20回踩不破": 22.0,
        "二波启动": 22.0,
        "50%位收阳": 20.0,
        "吞没": 20.0,
        "启明星": 20.0,
        "50%位higher_low": 18.0,
        "50%位放量": 16.0,
        "涨停突破下降通道": 22.0,
        "收盘站上EMA20": 16.0,
        "放量涨停": 20.0,
        "放量配合": 14.0,
    }
    signal_score = min(sum(signal_weights.get(signal, 0.0) for signal in candidate.signals), 44.0)
    return setup_score + signal_score


def score_trend_candidate(
    code: str,
    name: str,
    quote: dict[str, Any],
    bars: list[Bar],
    min_buy_sell_ratio: float,
) -> TrendCandidate | None:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    current = bars[-1]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma30 = sma(closes, 30)
    ma60 = sma(closes, 60)
    if ma5 is None or ma10 is None or ma20 is None or ma30 is None or ma60 is None:
        return None

    gain_30 = pct_change(current.close, closes[-31])
    gain_60 = pct_change(current.close, closes[-61])
    velocity_30 = gain_30 / 30
    buy_sell_ratio_60 = buy_sell_volume_ratio(bars, 60)
    confidence_bonus, confidence_factors = confidence_from_ema20_signal(bars, closes, volumes)
    structure_bullish_bonus, structure_bullish_factors, bearish_bonus, bearish_factors = confidence_from_reversal_structures(bars, closes)
    confidence_bonus += structure_bullish_bonus
    confidence_factors.extend(factor for factor in structure_bullish_factors if factor not in confidence_factors)
    context_bonus, context_factors, context_bearish_bonus, context_bearish_factors = brooks_context_adjustment(
        bars,
        closes,
        ma30,
        ma60,
    )
    confidence_bonus += context_bonus
    confidence_factors.extend(factor for factor in context_factors if factor not in confidence_factors)
    bearish_bonus += context_bearish_bonus
    bearish_factors.extend(factor for factor in context_bearish_factors if factor not in bearish_factors)
    volume_base = max(avg(volumes[-21:-1]), 1)
    moving_average_ok = ma5 > ma10 > ma20 and current.close > ma20 and ma20 > ma60 * 0.985
    if not moving_average_ok or gain_30 <= 0 or gain_60 <= 0 or buy_sell_ratio_60 < min_buy_sell_ratio:
        return None

    raw_bullish_confidence = (
        45.0
        + min(max(gain_60, 0), 80) * 0.18
        + min(max(gain_30, 0), 60) * 0.12
        + min(buy_sell_ratio_60, 5) * 3
        + confidence_bonus
    )
    bearish_confidence = min(95.0, 18.0 + bearish_bonus)
    bullish_confidence = min(
        95.0,
        max(5.0, raw_bullish_confidence - bearish_bonus * 0.45),
    )
    final_score = (
        gain_60
        + gain_30 * 0.35
        + min(buy_sell_ratio_60, 6) * 8
        + (current.volume / volume_base) * 2
        + confidence_bonus
        - bearish_bonus * 0.7
    )
    return TrendCandidate(
        code=code,
        name=name,
        date=current.date,
        close=current.close,
        pct_change=number(quote.get("changepercent")),
        amount=number(quote.get("amount"), 0) or 0,
        gain_30=gain_30,
        velocity_30=velocity_30,
        gain_60=gain_60,
        buy_sell_ratio_60=buy_sell_ratio_60,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma30=ma30,
        ma60=ma60,
        volume_ratio=current.volume / volume_base,
        bullish_confidence=bullish_confidence,
        confidence_factors=confidence_factors,
        bearish_confidence=bearish_confidence,
        bearish_factors=bearish_factors,
        final_score=final_score,
    )


def score_range_bound(
    code: str,
    name: str,
    quote: dict[str, Any],
    bars: list[Bar],
    min_buy_sell_ratio: float,
) -> RangeBoundCandidate | None:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    current = bars[-1]
    ma30 = sma(closes, 30)
    ma60 = sma(closes, 60)
    if ma30 is None or ma60 is None or len(bars) < 130:
        return None

    # 较宽的震荡区间（40-60日）
    range_lookback = min(60, int(len(bars) * 0.5))
    range_window = bars[-range_lookback:]
    range_low = min(bar.low for bar in range_window)
    range_high = max(bar.high for bar in range_window)
    range_width_pct = (range_high - range_low) / max(range_low, 1e-9) * 100
    if range_width_pct < 15:
        return None

    # 当前价在区间下沿 0-35% 位置
    range_height = max(range_high - range_low, 1e-9)
    range_position = (current.close - range_low) / range_height
    if not (0.0 <= range_position <= 0.35):
        return None

    # 盈亏比过滤：上方空间 >= 2x 下方止损风险
    stop = range_low * 0.995
    risk = current.close - stop
    reward = range_high - current.close
    if risk <= 0 or reward / risk < 2.0:
        return None

    # Al Brooks 价格行为下沿信号
    lower_edge_signals: list[str] = []
    if bullish_pinbar(current):
        lower_edge_signals.append("看涨Pinbar")
    if bullish_engulfing(bars):
        lower_edge_signals.append("吞没")
    if morning_star(bars):
        lower_edge_signals.append("启明星")
    if higher_low(bars):
        lower_edge_signals.append("更高低点")
    wedge, wedge_factors = three_push_wedge(bars, closes)
    if wedge:
        lower_edge_signals.append(wedge_factors[0] if wedge_factors else "三推楔形")

    if not lower_edge_signals:
        return None

    # 置信度
    gain_30 = pct_change(current.close, closes[-31]) if len(closes) >= 31 else 0
    gain_60 = pct_change(current.close, closes[-61]) if len(closes) >= 61 else 0
    buy_sell_ratio_60 = buy_sell_volume_ratio(bars, 60)
    volume_base = max(avg(volumes[-21:-1]), 1)
    volume_ratio = current.volume / volume_base

    confidence_factors = [
        f"宽幅震荡区间({range_width_pct:.0f}%)",
        f"接近区间下沿({range_position:.0%})",
    ]
    confidence_factors.extend(lower_edge_signals)

    if ma30 > ma60:
        confidence_factors.append("MA30在MA60上方")
    if buy_sell_ratio_60 >= min_buy_sell_ratio:
        confidence_factors.append(f"60日买盘强:{buy_sell_ratio_60:.2f}x")
    if volume_ratio <= 0.9:
        confidence_factors.append("下沿缩量")

    bullish_bonus, structure_bullish_factors, bearish_bonus, bearish_factors = confidence_from_reversal_structures(bars, closes)
    confidence_factors.extend(f for f in structure_bullish_factors if f not in confidence_factors)

    raw_bullish = (
        48.0
        + max(0.0, 1.0 - range_position) * 18
        + min(max(gain_60, 0), 80) * 0.06
        + min(buy_sell_ratio_60, 4) * 2
        + len(lower_edge_signals) * 5
    )
    bearish_confidence = min(95.0, 18.0 + bearish_bonus)
    bullish_confidence = min(96.0, max(5.0, raw_bullish - bearish_bonus * 0.45))
    final_score = (
        bullish_confidence
        + (1.0 - min(max(range_position, 0.0), 1.0)) * 15
        + min(buy_sell_ratio_60, 5) * 2
        + len(lower_edge_signals) * 4
        - max(bearish_confidence - 35, 0) * 0.25
    )

    return RangeBoundCandidate(
        code=code,
        name=name,
        date=current.date,
        close=current.close,
        pct_change=number(quote.get("changepercent")),
        amount=number(quote.get("amount"), 0) or 0,
        range_low=range_low,
        range_high=range_high,
        range_days=range_lookback,
        range_position=range_position,
        range_width_pct=range_width_pct,
        gain_30=gain_30,
        gain_60=gain_60,
        buy_sell_ratio_60=buy_sell_ratio_60,
        ma30=ma30,
        ma60=ma60,
        volume_ratio=volume_ratio,
        bullish_confidence=bullish_confidence,
        confidence_factors=confidence_factors,
        bearish_confidence=bearish_confidence,
        bearish_factors=bearish_factors,
        lower_edge_signals=lower_edge_signals,
    )
def score_price_action(
    code: str,
    name: str,
    quote: dict[str, Any],
    bars: list[Bar],
    min_buy_sell_ratio: float,
    allow_second_wave: bool = True,
) -> AShareCandidate | None:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    current = bars[-1]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma30 = sma(closes, 30)
    ma60 = sma(closes, 60)
    if ma5 is None or ma10 is None or ma20 is None or ma30 is None or ma60 is None:
        return None

    gain_30 = pct_change(current.close, closes[-31])
    gain_60 = pct_change(current.close, closes[-61])
    velocity_30 = gain_30 / 30
    buy_sell_ratio_60 = buy_sell_volume_ratio(bars, 60)
    confidence_bonus, confidence_factors = confidence_from_ema20_signal(bars, closes, volumes)
    structure_bullish_bonus, structure_bullish_factors, bearish_bonus, bearish_factors = confidence_from_reversal_structures(bars, closes)
    confidence_bonus += structure_bullish_bonus
    confidence_factors.extend(factor for factor in structure_bullish_factors if factor not in confidence_factors)

    previous_ma20 = sum(closes[-23:-3]) / 20
    ema20 = ema_series(closes, 20)[-1]
    ema20_before = ema_series(closes[:-3], 20)[-1]
    moving_average_ok = (
        ma20 > previous_ma20
        and current.close > ma20
        and ema20 is not None
        and (ema20_before is None or ema20 >= ema20_before * 0.995)
    )
    if not moving_average_ok:
        return None

    # EMA20 支撑趋势检查
    _, _, ema20_score, ema20_factors = ema20_support_strength(bars, closes)
    if ema20_score < 3.0:
        return None
    confidence_factors.extend(f for f in ema20_factors if f not in confidence_factors)

    prior_30 = bars[-31:-1]
    resistance_bar = max(prior_30, key=lambda bar: bar.high)

    # ── 策略一优先：回踩 50% 回调位 ────────────────────────────
    retrace_50, retrace_level, retrace_date, retrace_factors = detect_50pct_retracement(
        bars, closes, volumes
    )

    retest, broken_level, broken_date = recent_breakout_retest(bars)
    second_wave = False
    measured_target: float | None = None
    second_wave_factors: list[str] = []
    if not retest and allow_second_wave:
        second_wave, broken_level, broken_date, measured_target, second_wave_factors = recent_ema20_second_wave(
            bars,
            closes,
            volumes,
        )
    ema20_breakout = False
    ema20_breakout_factors: list[str] = []
    if not retest and not second_wave:
        ema20_breakout, broken_level, broken_date, ema20_breakout_factors = detect_ema20_breakout(
            bars,
            closes,
            volumes,
        )
    channel_breakout = False
    channel_breakout_factors: list[str] = []
    if not retrace_50 and not retest and not second_wave and not ema20_breakout:
        channel_breakout, broken_level, broken_date, channel_breakout_factors = detect_descending_channel_breakout(
            bars,
            closes,
            volumes,
        )
    if not retrace_50 and not retest and not second_wave and not ema20_breakout and not channel_breakout:
        return None

    # ── Setup 优先级：回踩50% > 二波EMA20 > 突破EMA20 > 下降通道突破 > 突破后回踩 ──
    if retrace_50:
        setup = "回踩50%"
        level = retrace_level
        broken_date = retrace_date
    elif second_wave:
        setup = "二波回踩EMA20"
        level = broken_level
    elif ema20_breakout:
        setup = "突破EMA20"
        level = broken_level
    elif channel_breakout:
        setup = "下降通道突破"
        level = broken_level
    else:
        setup = "突破后回踩"
        level = broken_level

    if level is None:
        return None

    signals: list[str] = []
    if bullish_engulfing(bars):
        signals.append("吞没")
    if morning_star(bars):
        signals.append("启明星")
    if not second_wave and not ema20_breakout and strong_breakout_bar(bars, level):
        signals.append("放量实体突破")
    if retest and retest_hold_signal(current, level):
        signals.append("回踩不破")
    if retrace_50:
        signals.extend(f for f in retrace_factors if not f.startswith("50%回调"))
    if second_wave:
        signals.extend(["EMA20回踩不破", "二波启动"])
    if ema20_breakout:
        signals.extend(["放量突破EMA20"])
    if channel_breakout:
        signals.extend(f for f in channel_breakout_factors if f not in signals)
    if not signals:
        return None

    if setup in {"回踩50%", "突破后回踩", "二波回踩EMA20"}:
        wedge, wedge_factors = three_push_wedge(bars, closes)
        if wedge:
            confidence_bonus += 18.0
            confidence_factors.extend(f for f in wedge_factors if f not in confidence_factors)

    volume_base = max(avg(volumes[-21:-1]), 1)
    confirmation_bonus, confirmation_factors, confirmation_bearish_bonus, confirmation_bearish_factors = brooks_breakout_confirmation(
        bars,
        level,
        broken_date,
        setup,
    )
    confidence_bonus += confirmation_bonus
    confidence_factors.extend(factor for factor in confirmation_factors if factor not in confidence_factors)
    bearish_bonus += confirmation_bearish_bonus
    bearish_factors.extend(factor for factor in confirmation_bearish_factors if factor not in bearish_factors)
    price_action_bonus, price_action_factors = confidence_from_price_action(setup, signals)
    confidence_bonus += price_action_bonus
    confidence_factors.extend(factor for factor in price_action_factors if factor not in confidence_factors)
    if retrace_50:
        confidence_bonus += 10.0  # 50% 回调优先级最高
        confidence_factors.extend(factor for factor in retrace_factors if factor not in confidence_factors)
    elif second_wave:
        confidence_bonus += 8.0
        confidence_factors.extend(factor for factor in second_wave_factors if factor not in confidence_factors)
    else:
        breakout_bonus, breakout_factors = confidence_from_volume_breakout(bars, level, volume_base)
        confidence_bonus += breakout_bonus
        confidence_factors.extend(factor for factor in breakout_factors if factor not in confidence_factors)

    candidates = [(date, high) for date, high in swing_highs(bars[:-5]) if high > current.close * 1.01]
    for period in (120, 250):
        segment = bars[-min(period, len(bars)) : -5]
        if segment:
            high_bar = max(segment, key=lambda bar: bar.high)
            if high_bar.high > current.close * 1.01:
                candidates.append((high_bar.date, high_bar.high))
    if not candidates and measured_target is None:
        return None

    if candidates:
        target_date, target = sorted(set(candidates), key=lambda item: item[1])[0]
        for candidate_date, candidate_target in sorted(set(candidates), key=lambda item: item[1]):
            if candidate_target >= current.close * 1.025:
                target_date = candidate_date
                target = candidate_target
                break
        measured_target_from_breakout, measured_target_label = measured_move_target_from_breakout(
            bars,
            level,
            broken_date,
            current.close,
        )
        if measured_target_from_breakout is not None and measured_target_from_breakout < target:
            target = measured_target_from_breakout
            target_date = measured_target_label or "箱体量度目标"
            confidence_factors.append("目标按量度运动保守校准")
    else:
        target_date = "二波量度目标"
        target = measured_target or current.close * 1.06

    stop = structure_stop_for_setup(bars, current, level, setup)
    if stop is None:
        bearish_factors.append("结构止损超过5%")
        return None
    risk = current.close - stop
    reward = target - current.close
    reward_risk = reward / risk if risk else 0
    if reward_risk <= 1.0:
        return None
    rr_confidence = reward_risk_confidence(reward_risk) or 0.0

    raw_bullish_confidence = (
        42.0
        + min(reward_risk * 5, 20)
        + min(max(gain_60, 0), 60) * 0.12
        + min(current.volume / volume_base, 4) * 2
        + confidence_bonus
    )
    bearish_confidence = min(98.0, 20.0 + bearish_bonus)
    bullish_confidence = min(
        98.0,
        max(5.0, raw_bullish_confidence - bearish_bonus * 0.5),
    )
    return AShareCandidate(
        code=code,
        name=name,
        date=current.date,
        close=current.close,
        pct_change=number(quote.get("changepercent")),
        amount=number(quote.get("amount"), 0) or 0,
        setup=setup,
        signals=signals,
        support=level,
        support_date=broken_date or resistance_bar.date,
        stop=stop,
        target=target,
        target_date=target_date,
        reward_risk=reward_risk,
        reward_risk_confidence=rr_confidence,
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma30=ma30,
        ma60=ma60,
        volume_ratio=current.volume / volume_base,
        gain_30=gain_30,
        velocity_30=velocity_30,
        gain_60=gain_60,
        buy_sell_ratio_60=buy_sell_ratio_60,
        bullish_confidence=bullish_confidence,
        confidence_factors=confidence_factors,
        bearish_confidence=bearish_confidence,
        bearish_factors=bearish_factors,
        false_breaks=false_breakout_count(bars, level),
    )


def process_quote(
    quote: dict[str, Any],
    min_buy_sell_ratio: float,
) -> tuple[AShareCandidate | None, TrendCandidate | None, RangeBoundCandidate | None, str | None]:
    code = str(quote.get("code", ""))
    name = str(quote.get("name", code))
    try:
        bars = fetch_daily_bars(code)
        price_action = score_price_action(code, name, quote, bars, min_buy_sell_ratio)
        trend = score_trend_candidate(code, name, quote, bars, min_buy_sell_ratio)
        wyckoff = score_range_bound(code, name, quote, bars, min_buy_sell_ratio)
        return price_action, trend, wyckoff, None
    except Exception as exc:
        return None, None, None, f"{code} {name}: {exc}"


def process_t0_fund_quote(
    quote: dict[str, Any],
    min_buy_sell_ratio: float,
) -> tuple[T0FundCandidate | None, str | None]:
    code = str(quote.get("code", ""))
    name = str(quote.get("name", code))
    try:
        bars = fetch_daily_bars(code)
        candidate = score_price_action(code, name, quote, bars, min_buy_sell_ratio, allow_second_wave=False)
        if not candidate:
            return None, None
        return T0FundCandidate(candidate=candidate, t0_reason=str(quote.get("t0_reason") or "T+0场内基金观察")), None
    except Exception as exc:
        return None, f"T+0基金 {code} {name}: {exc}"


def parse_hq_indices() -> list[MarketIndex]:
    text = fetch_text(SINA_HQ_URL.format(symbols="sh000001,sz399001,sz399006,sh000300,sh000852"), retries=3)
    indices: list[MarketIndex] = []
    for symbol, payload in re.findall(r"hq_str_(\w+)=\"([^\"]*)\"", text):
        parts = payload.split(",")
        if len(parts) < 10:
            continue
        name = parts[0]
        previous_close = number(parts[2])
        current = number(parts[3])
        amount = number(parts[9])
        if previous_close and current:
            indices.append(
                MarketIndex(
                    symbol=symbol,
                    name=name,
                    price=current,
                    change_pct=((current - previous_close) / previous_close) * 100,
                    amount=amount,
                )
            )
    return indices


def lookup_cfi_stock_id(code: str) -> str | None:
    try:
        page = fetch_text(CFI_QUOTE_URL.format(code=code), retries=1, timeout=8)
    except RuntimeError:
        return None
    matches = re.findall(r"stockid=(\d+)", page)
    return matches[0] if matches else None


def parse_section_rows(text: str) -> list[SectionStrength]:
    rows: list[SectionStrength] = []
    cleaned = clean_text(text)
    section = cleaned
    marker = "版块名称"
    if marker in cleaned:
        section = cleaned[cleaned.find(marker) :]
    section = section[: section.find("转至")] if "转至" in section else section
    pattern = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9.]+)\s+(\d+)\s+(\d+)\s+([+-]?\d+\.\d+)%")
    for name, up_count, down_count, avg_change in pattern.findall(section):
        if name in {"版块名称", "上涨家数", "下跌家数", "平均涨幅"}:
            continue
        rows.append(SectionStrength(name, int(up_count), int(down_count), float(avg_change)))
    return rows


def parse_industry_strength(text: str, stock_name: str, code: str) -> SectionStrength | None:
    cleaned = clean_text(text)
    industry_matches = re.findall(
        re.escape(stock_name) + r"\(" + re.escape(code) + r"\)([\u4e00-\u9fa5A-Za-z0-9和]+)\s+上涨家数",
        cleaned,
    )
    avg_match = re.search(r"上涨家数:\s*(\d+)\s+下跌家数:\s*(\d+)\s+平均涨幅:\s*([+-]?\d+\.\d+)%", cleaned)
    if not avg_match:
        return None
    industry_name = industry_matches[-1].strip() if industry_matches else "同行业"
    return SectionStrength(industry_name, int(avg_match.group(1)), int(avg_match.group(2)), float(avg_match.group(3)))


def latest_company_note(code: str, stock_name: str) -> str | None:
    try:
        page = fetch_text(CFI_QUOTE_URL.format(code=code), retries=1, timeout=8)
    except RuntimeError:
        return None
    cleaned = clean_text(page)
    announcement = re.search(r"最新公告：\s*(.*?)(?:\s*\(更多\)|\s+分时|\s+日K|$)", cleaned)
    profit = re.search(r"净利润\(亿\)\s*([\-0-9. ]{8,80})", cleaned)
    notes: list[str] = []
    if announcement:
        notes.append(f"最新公告：{announcement.group(1).strip()[:80]}")
    if profit:
        notes.append(f"利润序列：{profit.group(1).strip()}")
    if not notes:
        return None
    return "；".join(notes[:2])


def parse_guba_time(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def community_keywords_score(title: str) -> tuple[int, int, int]:
    bullish_terms = (
        "涨停",
        "主升",
        "突破",
        "加仓",
        "反弹",
        "企稳",
        "拉升",
        "起飞",
        "新高",
        "牛市",
        "强势",
        "资金流入",
        "上车",
    )
    bearish_terms = (
        "诱多",
        "发套",
        "出货",
        "跑路",
        "清仓",
        "割肉",
        "暴跌",
        "砸盘",
        "套牢",
        "深套",
        "别买",
        "不要买",
        "还得跌",
        "破位",
    )
    hype_terms = (
        "满仓",
        "梭哈",
        "无脑",
        "必涨",
        "必板",
        "翻倍",
        "十倍",
        "目标",
        "涨停",
        "冲破",
        "开干",
        "干就完",
        "赶紧买",
        "上车",
        "不要错过",
        "大牛",
        "暴涨",
    )
    bullish = sum(1 for term in bullish_terms if term in title)
    bearish = sum(1 for term in bearish_terms if term in title)
    hype = sum(1 for term in hype_terms if term in title)
    if re.search(r"\d+(\.\d+)?\s*[元万倍]?目标|目标[:：]?\s*\d", title):
        hype += 1
    return bullish, bearish, hype


def fetch_guba_community_signal(code: str) -> CommunitySignal | None:
    try:
        page = fetch_text(EASTMONEY_GUBA_URL.format(code=code), retries=1, timeout=10)
    except RuntimeError:
        return None

    payload = extract_js_object(page, "article_list")
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    posts = data.get("re") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        return None

    now = dt.datetime.now()
    recent_posts = 0
    read_count = 0
    comment_count = 0
    bullish_posts = 0
    bearish_posts = 0
    lure_posts = 0
    sample_titles: list[str] = []
    lure_titles: list[str] = []

    for post in posts:
        if not isinstance(post, dict):
            continue
        title = clean_title(str(post.get("post_title") or post.get("post_content") or ""))
        if not title:
            continue
        publish_time = parse_guba_time(str(post.get("post_publish_time") or post.get("post_display_time") or ""))
        if publish_time and now - publish_time <= dt.timedelta(hours=24):
            recent_posts += 1
        read_count += int(number(post.get("post_click_count"), 0) or 0)
        comment_count += int(number(post.get("post_comment_count"), 0) or 0)
        bullish, bearish, hype = community_keywords_score(title)
        if bullish:
            bullish_posts += 1
        if bearish:
            bearish_posts += 1
        if hype:
            lure_posts += 1
            if len(lure_titles) < 3:
                lure_titles.append(title[:34])
        if len(sample_titles) < 3:
            sample_titles.append(title[:34])

    total_posts = int(number(data.get("count"), 0) or 0) if isinstance(data, dict) and data.get("count") is not None else None
    post_base = max(len([post for post in posts if isinstance(post, dict)]), 1)
    discussion_score = min(
        10.0,
        recent_posts * 0.07
        + math.log1p(max(read_count, 0)) * 0.45
        + math.log1p(max(comment_count, 0)) * 0.40,
    )
    sentiment_score = max(-8.0, min(8.0, (bullish_posts - bearish_posts) / post_base * 12.0))
    hype_risk_score = min(10.0, lure_posts / post_base * 18.0 + math.log1p(max(comment_count, 0)) * 0.15)

    return CommunitySignal(
        source="东方财富股吧",
        recent_posts=recent_posts,
        total_posts=total_posts,
        read_count=read_count,
        comment_count=comment_count,
        bullish_posts=bullish_posts,
        bearish_posts=bearish_posts,
        lure_posts=lure_posts,
        discussion_score=discussion_score,
        sentiment_score=sentiment_score,
        hype_risk_score=hype_risk_score,
        sample_titles=sample_titles,
        lure_titles=lure_titles,
    )


def fetch_stock_context(code: str, name: str) -> tuple[SectionStrength | None, list[SectionStrength], str | None]:
    stock_id = lookup_cfi_stock_id(code)
    industry: SectionStrength | None = None
    concepts: list[SectionStrength] = []
    if stock_id:
        try:
            industry_page = fetch_text(
                CFI_SECTION_URL,
                {"client": "phone", "contenttype": "same_hy", "stockid": stock_id},
                retries=1,
                timeout=8,
            )
            industry = parse_industry_strength(industry_page, name, code)
        except RuntimeError:
            industry = None
        try:
            concept_page = fetch_text(
                CFI_SECTION_URL,
                {"client": "phone", "contenttype": "same_gn", "stockid": stock_id},
                retries=1,
                timeout=8,
            )
            concepts = parse_section_rows(concept_page)[:8]
        except RuntimeError:
            concepts = []
    return industry, concepts, latest_company_note(code, name)


def section_breadth_score(section: SectionStrength | None) -> float:
    if not section or section.up_count is None or section.down_count is None:
        return 0.0
    total = section.up_count + section.down_count
    if total <= 0:
        return 0.0
    return (section.up_count - section.down_count) / total


def sector_strength_score(industry: SectionStrength | None, concepts: list[SectionStrength]) -> tuple[float, str | None]:
    industry_change = industry.avg_change_pct if industry and industry.avg_change_pct is not None else 0.0
    industry_breadth = section_breadth_score(industry)
    valid_concepts = [item for item in concepts if item.avg_change_pct is not None]
    strongest = max(valid_concepts, key=lambda item: item.avg_change_pct) if valid_concepts else None
    strongest_change = strongest.avg_change_pct if strongest and strongest.avg_change_pct is not None else 0.0
    top_concepts = sorted(valid_concepts, key=lambda item: item.avg_change_pct or -99, reverse=True)[:3]
    concept_average = avg([item.avg_change_pct or 0.0 for item in top_concepts]) if top_concepts else 0.0
    strongest_breadth = section_breadth_score(strongest)
    score = (
        industry_change * 1.2
        + strongest_change * 1.4
        + concept_average * 0.6
        + industry_breadth * 2.0
        + strongest_breadth * 1.5
    )
    label = None
    if strongest:
        label = f"{strongest.name} {fmt_pct(strongest.avg_change_pct)}"
    elif industry:
        label = f"{industry.name} {fmt_pct(industry.avg_change_pct)}"
    return score, label


def apply_sector_confidence(
    bullish_confidence: float,
    confidence_factors: list[str],
    bearish_confidence: float,
    bearish_factors: list[str],
    industry: SectionStrength | None,
    concepts: list[SectionStrength],
) -> tuple[float, list[str], float, list[str], float]:
    sector_score, label = sector_strength_score(industry, concepts)
    adjusted_bullish = bullish_confidence
    adjusted_bearish = bearish_confidence
    adjusted_factors = list(confidence_factors)
    adjusted_bearish_factors = list(bearish_factors)

    if sector_score >= 6:
        adjusted_bullish += 8
        adjusted_factors.append(f"强板块共振:{label or '板块'}")
    elif sector_score >= 3:
        adjusted_bullish += 5
        adjusted_factors.append(f"板块偏强:{label or '板块'}")
    elif sector_score <= -5:
        adjusted_bullish -= 8
        adjusted_bearish += 8
        adjusted_bearish_factors.append(f"板块明显转弱:{label or '板块'}")
    elif sector_score <= -2.5:
        adjusted_bullish -= 4
        adjusted_bearish += 4
        adjusted_bearish_factors.append(f"板块偏弱:{label or '板块'}")

    return (
        min(98.0, max(5.0, adjusted_bullish)),
        adjusted_factors,
        min(98.0, max(0.0, adjusted_bearish)),
        adjusted_bearish_factors,
        sector_score,
    )


def apply_community_confidence(
    bullish_confidence: float,
    confidence_factors: list[str],
    bearish_confidence: float,
    bearish_factors: list[str],
    community: CommunitySignal | None,
) -> tuple[float, list[str], float, list[str], float]:
    adjusted_bullish = bullish_confidence
    adjusted_bearish = bearish_confidence
    adjusted_factors = list(confidence_factors)
    adjusted_bearish_factors = list(bearish_factors)
    if not community:
        return adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, 0.0

    community_score = 0.0
    overheated = community.hype_risk_score >= 3.5 or community.lure_posts >= 3
    if community.discussion_score >= 6.0 and community.sentiment_score > -1.0 and not overheated:
        adjusted_bullish += 2.5
        adjusted_factors.append(f"股吧讨论活跃:{community.recent_posts}帖/24h")
        community_score += 2.0
    elif community.discussion_score <= 2.0:
        community_score -= 0.5

    if community.sentiment_score >= 3.0 and not overheated:
        adjusted_bullish += 1.5
        adjusted_factors.append("股吧情绪偏多")
        community_score += 1.0
    elif community.sentiment_score <= -3.0:
        adjusted_bullish -= 1.5
        adjusted_bearish += 1.5
        adjusted_bearish_factors.append("股吧情绪偏空")
        community_score -= 1.0

    if overheated:
        adjusted_bullish -= 2.0
        adjusted_bearish += 3.0
        adjusted_bearish_factors.append("股吧疑似诱多/过热话术")
        community_score -= 2.0

    return (
        min(98.0, max(5.0, adjusted_bullish)),
        adjusted_factors,
        min(98.0, max(0.0, adjusted_bearish)),
        adjusted_bearish_factors,
        max(-4.0, min(3.0, community_score)),
    )


def enrich_candidate(candidate: AShareCandidate) -> AShareCandidate:
    if SKIP_EXTERNAL_CONTEXT:
        return candidate
    industry, concepts, note = fetch_stock_context(candidate.code, candidate.name)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, sector_score = apply_sector_confidence(
        candidate.bullish_confidence,
        candidate.confidence_factors,
        candidate.bearish_confidence,
        candidate.bearish_factors,
        industry,
        concepts,
    )
    community = fetch_guba_community_signal(candidate.code)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, community_score = apply_community_confidence(
        adjusted_bullish,
        adjusted_factors,
        adjusted_bearish,
        adjusted_bearish_factors,
        community,
    )
    news_penalty = 0.0
    if note and any(token in note for token in ("亏损", "-")):
        news_penalty -= 0.5
    price_action_score = price_action_rank_score(candidate)
    trend_score = candidate.gain_60 * 0.06 + candidate.velocity_30 * 0.8 + min(candidate.buy_sell_ratio_60, 5) * 0.8
    final_score = (
        price_action_score
        + candidate.reward_risk * 6
        + max(min(sector_score, 12), -10) * 1.2
        + community_score
        + candidate.volume_ratio * 2
        + trend_score
        + (adjusted_bullish - 50) * 0.15
        - max(adjusted_bearish - 35, 0) * 0.25
        - candidate.false_breaks * 2.0
        + news_penalty
    )
    return replace(
        candidate,
        industry=industry,
        concepts=concepts,
        community=community,
        latest_note=note,
        final_score=final_score,
        bullish_confidence=adjusted_bullish,
        confidence_factors=adjusted_factors,
        bearish_confidence=adjusted_bearish,
        bearish_factors=adjusted_bearish_factors,
    )


def enrich_trend_candidate(candidate: TrendCandidate) -> TrendCandidate:
    if SKIP_EXTERNAL_CONTEXT:
        return candidate
    industry, concepts, note = fetch_stock_context(candidate.code, candidate.name)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, sector_score = apply_sector_confidence(
        candidate.bullish_confidence,
        candidate.confidence_factors,
        candidate.bearish_confidence,
        candidate.bearish_factors,
        industry,
        concepts,
    )
    community = fetch_guba_community_signal(candidate.code)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, community_score = apply_community_confidence(
        adjusted_bullish,
        adjusted_factors,
        adjusted_bearish,
        adjusted_bearish_factors,
        community,
    )
    final_score = (
        candidate.final_score
        + max(min(sector_score, 12), -10) * 1.2
        + community_score
        + (adjusted_bullish - 50) * 0.2
        - max(adjusted_bearish - 35, 0) * 0.25
    )
    return replace(
        candidate,
        industry=industry,
        concepts=concepts,
        community=community,
        latest_note=note,
        final_score=final_score,
        bullish_confidence=adjusted_bullish,
        confidence_factors=adjusted_factors,
        bearish_confidence=adjusted_bearish,
        bearish_factors=adjusted_bearish_factors,
    )


def enrich_range_bound_candidate(candidate: RangeBoundCandidate) -> RangeBoundCandidate:
    if SKIP_EXTERNAL_CONTEXT:
        return candidate
    industry, concepts, note = fetch_stock_context(candidate.code, candidate.name)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, sector_score = apply_sector_confidence(
        candidate.bullish_confidence,
        candidate.confidence_factors,
        candidate.bearish_confidence,
        candidate.bearish_factors,
        industry,
        concepts,
    )
    community = fetch_guba_community_signal(candidate.code)
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, community_score = apply_community_confidence(
        adjusted_bullish,
        adjusted_factors,
        adjusted_bearish,
        adjusted_bearish_factors,
        community,
    )
    final_score = (
        candidate.final_score
        + max(min(sector_score, 10), -8) * 0.8
        + community_score
        + (adjusted_bullish - 60) * 0.12
        - max(adjusted_bearish - 35, 0) * 0.22
    )
    return replace(
        candidate,
        industry=industry,
        concepts=concepts,
        community=community,
        latest_note=note,
        final_score=final_score,
        bullish_confidence=adjusted_bullish,
        confidence_factors=adjusted_factors,
        bearish_confidence=adjusted_bearish,
        bearish_factors=adjusted_bearish_factors,
    )


def watchlist_review_from_price_action(candidate: AShareCandidate) -> WatchlistReview:
    enriched = enrich_candidate(candidate)
    status = "符合策略" if enriched.bullish_confidence >= 75 and enriched.bearish_confidence < 55 else "触发但谨慎"
    return WatchlistReview(
        code=enriched.code,
        name=enriched.name,
        date=enriched.date,
        close=enriched.close,
        pct_change=enriched.pct_change,
        amount=enriched.amount,
        status=status,
        setup=enriched.setup,
        signals=enriched.signals,
        support=enriched.support,
        target=enriched.target,
        reward_risk=enriched.reward_risk,
        reward_risk_confidence=enriched.reward_risk_confidence,
        ma5=enriched.ma5,
        ma10=enriched.ma10,
        ma20=enriched.ma20,
        ma30=enriched.ma30,
        ma60=enriched.ma60,
        volume_ratio=enriched.volume_ratio,
        gain_30=enriched.gain_30,
        velocity_30=enriched.velocity_30,
        gain_60=enriched.gain_60,
        buy_sell_ratio_60=enriched.buy_sell_ratio_60,
        bullish_confidence=enriched.bullish_confidence,
        confidence_factors=enriched.confidence_factors,
        bearish_confidence=enriched.bearish_confidence,
        bearish_factors=enriched.bearish_factors,
        comment=trade_comment(enriched),
        industry=enriched.industry,
        concepts=enriched.concepts,
        community=enriched.community,
        latest_note=enriched.latest_note,
        final_score=enriched.final_score + 40,
    )


def watchlist_review_from_trend(candidate: TrendCandidate) -> WatchlistReview:
    enriched = enrich_trend_candidate(candidate)
    return WatchlistReview(
        code=enriched.code,
        name=enriched.name,
        date=enriched.date,
        close=enriched.close,
        pct_change=enriched.pct_change,
        amount=enriched.amount,
        status="趋势观察",
        setup="等待裸K触发",
        signals=[],
        support=None,
        target=None,
        reward_risk=None,
        reward_risk_confidence=None,
        ma5=enriched.ma5,
        ma10=enriched.ma10,
        ma20=enriched.ma20,
        ma30=enriched.ma30,
        ma60=enriched.ma60,
        volume_ratio=enriched.volume_ratio,
        gain_30=enriched.gain_30,
        velocity_30=enriched.velocity_30,
        gain_60=enriched.gain_60,
        buy_sell_ratio_60=enriched.buy_sell_ratio_60,
        bullish_confidence=enriched.bullish_confidence,
        confidence_factors=enriched.confidence_factors,
        bearish_confidence=enriched.bearish_confidence,
        bearish_factors=enriched.bearish_factors,
        comment=f"{trend_comment(enriched)} 未触发裸K建仓条件。",
        industry=enriched.industry,
        concepts=enriched.concepts,
        community=enriched.community,
        latest_note=enriched.latest_note,
        final_score=enriched.final_score,
    )


def watchlist_review_from_range_bound(candidate: RangeBoundCandidate) -> WatchlistReview:
    enriched = enrich_range_bound_candidate(candidate)
    comment = range_bound_comment(enriched)
    return WatchlistReview(
        code=enriched.code,
        name=enriched.name,
        date=enriched.date,
        close=enriched.close,
        pct_change=enriched.pct_change,
        amount=enriched.amount,
        status="策略二观察",
        setup="策略二:震荡区间下沿",
        signals=enriched.lower_edge_signals,
        support=enriched.range_low,
        target=enriched.range_high,
        reward_risk=None,
        reward_risk_confidence=None,
        ma5=None,
        ma10=None,
        ma20=None,
        ma30=enriched.ma30,
        ma60=enriched.ma60,
        volume_ratio=enriched.volume_ratio,
        gain_30=enriched.gain_30,
        velocity_30=None,
        gain_60=enriched.gain_60,
        buy_sell_ratio_60=enriched.buy_sell_ratio_60,
        bullish_confidence=enriched.bullish_confidence,
        confidence_factors=enriched.confidence_factors,
        bearish_confidence=enriched.bearish_confidence,
        bearish_factors=enriched.bearish_factors,
        comment=comment,
        industry=enriched.industry,
        concepts=enriched.concepts,
        community=enriched.community,
        latest_note=enriched.latest_note,
        final_score=enriched.final_score + 25,
    )


def diagnose_watchlist_quote(
    code: str,
    name: str,
    quote: dict[str, Any],
    bars: list[Bar],
    min_buy_sell_ratio: float,
) -> WatchlistReview:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    current = bars[-1]
    ma5 = sma(closes, 5)
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma30 = sma(closes, 30)
    ma60 = sma(closes, 60)
    gain_30 = pct_change(current.close, closes[-31]) if len(closes) >= 31 else None
    gain_60 = pct_change(current.close, closes[-61]) if len(closes) >= 61 else None
    velocity_30 = gain_30 / 30 if gain_30 is not None else None
    buy_sell_ratio_60 = buy_sell_volume_ratio(bars, 60) if len(bars) >= 60 else None
    volume_base = max(avg(volumes[-21:-1]), 1) if len(volumes) >= 21 else 1
    volume_ratio = current.volume / volume_base if volume_base else None

    if ma5 is None or ma10 is None or ma20 is None or ma30 is None or ma60 is None:
        return WatchlistReview(
            code=code,
            name=name,
            date=current.date,
            close=current.close,
            pct_change=number(quote.get("changepercent")),
            amount=number(quote.get("amount"), 0) or 0,
            status="数据不足",
            setup="-",
            signals=[],
            support=None,
            target=None,
            reward_risk=None,
            reward_risk_confidence=None,
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma30=ma30,
            ma60=ma60,
            volume_ratio=volume_ratio,
            gain_30=gain_30,
            velocity_30=velocity_30,
            gain_60=gain_60,
            buy_sell_ratio_60=buy_sell_ratio_60,
            bullish_confidence=None,
            confidence_factors=[],
            bearish_confidence=None,
            bearish_factors=[],
            comment="日K历史不足，无法完整检查策略。",
            final_score=-20,
        )

    previous_ma20 = sum(closes[-23:-3]) / 20
    ema20 = ema_series(closes, 20)[-1]
    ema20_before = ema_series(closes[:-3], 20)[-1]
    moving_average_ok = ma20 > previous_ma20 and current.close > ma20 and ema20 is not None and (ema20_before is None or ema20 >= ema20_before * 0.995)
    prior_30 = bars[-31:-1]
    resistance_bar = max(prior_30, key=lambda bar: bar.high)
    resistance = resistance_bar.high
    broke_now = current.close > resistance * 1.003
    retest, broken_level, broken_date = recent_breakout_retest(bars)
    second_wave = False
    measured_target: float | None = None
    second_wave_factors: list[str] = []
    if not retest:
        second_wave, second_level, broken_date, measured_target, second_wave_factors = recent_ema20_second_wave(
            bars,
            closes,
            volumes,
        )
        if second_wave:
            broken_level = second_level
    level = resistance if broke_now else broken_level
    signals: list[str] = []
    if bullish_engulfing(bars):
        signals.append("吞没")
    if morning_star(bars):
        signals.append("启明星")
    if level is not None and not second_wave and strong_breakout_bar(bars, level):
        signals.append("放量实体突破")
    if retest and level is not None and retest_hold_signal(current, level):
        signals.append("回踩不破")
    if second_wave:
        signals.extend(["EMA20回踩不破", "二波启动"])

    target: float | None = None
    reward_risk: float | None = None
    setup = "二波回踩EMA20" if second_wave else "突破" if broke_now else "突破后回踩" if retest else "等待突破/回踩"
    target_candidates = [(date, high) for date, high in swing_highs(bars[:-5]) if high > current.close * 1.01]
    for period in (120, 250):
        segment = bars[-min(period, len(bars)) : -5]
        if segment:
            high_bar = max(segment, key=lambda bar: bar.high)
            if high_bar.high > current.close * 1.01:
                target_candidates.append((high_bar.date, high_bar.high))
    if target_candidates:
        target = sorted(set(target_candidates), key=lambda item: item[1])[0][1]
        for _, candidate_target in sorted(set(target_candidates), key=lambda item: item[1]):
            if candidate_target >= current.close * 1.025:
                target = candidate_target
                break
        if level is not None:
            measured_target_from_breakout, _ = measured_move_target_from_breakout(bars, level, broken_date, current.close)
            if measured_target_from_breakout is not None and measured_target_from_breakout < target:
                target = measured_target_from_breakout
    elif measured_target is not None:
        target = measured_target
    if target is not None and level is not None:
        stop = structure_stop_for_setup(bars, current, level, setup)
        reward_risk = (target - current.close) / (current.close - stop) if stop is not None and current.close > stop else None

    if not moving_average_ok:
        status = "未触发"
        comment = "均线结构不达标：需要MA20和MA30上行，且收盘价在MA20上方。"
        score = 5.0
    elif not broke_now and not retest and not second_wave:
        status = "未触发"
        comment = "尚未出现突破后回踩不破或强趋势二波回踩EMA20的右侧确认。"
        score = 18.0
    elif not signals:
        status = "接近触发"
        comment = "结构接近，但缺少吞没、启明星、放量实体突破或回踩不破等看涨K线确认。"
        score = 35.0
    elif reward_risk is None:
        status = "接近触发"
        comment = "已有结构/K线触发，但结构止损超过5%或目标空间无法确认。"
        score = 32.0
    elif reward_risk is not None and reward_risk <= 1.0:
        status = "接近触发"
        comment = "已有结构/K线触发，但按结构止损计算，上方压力空间不足。"
        score = 32.0
    else:
        status = "接近触发"
        comment = "接近策略条件，但未进入严格候选，建议等收盘确认和次日回踩不破。"
        score = 38.0

    confidence_bonus, confidence_factors = confidence_from_ema20_signal(bars, closes, volumes)
    structure_bonus, structure_factors, bearish_bonus, bearish_factors = confidence_from_reversal_structures(bars, closes)
    context_bonus, context_factors, context_bearish_bonus, context_bearish_factors = brooks_context_adjustment(bars, closes, ma30, ma60)
    confirmation_bonus, confirmation_factors, confirmation_bearish_bonus, confirmation_bearish_factors = (
        brooks_breakout_confirmation(bars, level, broken_date, setup) if level is not None else (0.0, [], 0.0, [])
    )
    confidence_factors.extend(factor for factor in structure_factors if factor not in confidence_factors)
    confidence_factors.extend(factor for factor in context_factors if factor not in confidence_factors)
    confidence_factors.extend(factor for factor in confirmation_factors if factor not in confidence_factors)
    confidence_factors.extend(factor for factor in second_wave_factors if factor not in confidence_factors)
    bearish_bonus += context_bearish_bonus + confirmation_bearish_bonus
    bearish_factors.extend(factor for factor in context_bearish_factors if factor not in bearish_factors)
    bearish_factors.extend(factor for factor in confirmation_bearish_factors if factor not in bearish_factors)
    bullish_confidence = min(
        95.0,
        max(5.0, 40 + confidence_bonus + structure_bonus + context_bonus + confirmation_bonus - bearish_bonus * 0.45),
    )
    bearish_confidence = min(95.0, 18 + bearish_bonus)
    if status == "接近触发":
        score += min(bullish_confidence, 85) * 0.15

    return WatchlistReview(
        code=code,
        name=name,
        date=current.date,
        close=current.close,
        pct_change=number(quote.get("changepercent")),
        amount=number(quote.get("amount"), 0) or 0,
        status=status,
        setup=setup,
        signals=signals,
        support=level,
        target=target,
        reward_risk=reward_risk,
        reward_risk_confidence=reward_risk_confidence(reward_risk),
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma30=ma30,
        ma60=ma60,
        volume_ratio=volume_ratio,
        gain_30=gain_30,
        velocity_30=velocity_30,
        gain_60=gain_60,
        buy_sell_ratio_60=buy_sell_ratio_60,
        bullish_confidence=bullish_confidence,
        confidence_factors=confidence_factors,
        bearish_confidence=bearish_confidence,
        bearish_factors=bearish_factors,
        comment=comment,
        final_score=score,
    )


def enrich_watchlist_review_context(review: WatchlistReview) -> WatchlistReview:
    if review.industry or review.community:
        return review
    if SKIP_EXTERNAL_CONTEXT:
        return review
    industry, concepts, note = fetch_stock_context(review.code, review.name)
    community = fetch_guba_community_signal(review.code)
    bullish = review.bullish_confidence if review.bullish_confidence is not None else 40.0
    bearish = review.bearish_confidence if review.bearish_confidence is not None else 18.0
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, sector_score = apply_sector_confidence(
        bullish,
        review.confidence_factors,
        bearish,
        review.bearish_factors,
        industry,
        concepts,
    )
    adjusted_bullish, adjusted_factors, adjusted_bearish, adjusted_bearish_factors, community_score = apply_community_confidence(
        adjusted_bullish,
        adjusted_factors,
        adjusted_bearish,
        adjusted_bearish_factors,
        community,
    )
    return replace(
        review,
        industry=industry,
        concepts=concepts,
        community=community,
        latest_note=note,
        bullish_confidence=adjusted_bullish,
        confidence_factors=adjusted_factors,
        bearish_confidence=adjusted_bearish,
        bearish_factors=adjusted_bearish_factors,
        final_score=review.final_score + max(min(sector_score, 8), -8) * 0.8 + community_score,
    )


def process_watchlist_quote(
    quote: dict[str, Any],
    min_buy_sell_ratio: float,
) -> tuple[WatchlistReview | None, str | None]:
    code = str(quote.get("code", ""))
    name = str(quote.get("name", code))
    try:
        bars = fetch_daily_bars(code)
        price_action = score_price_action(code, name, quote, bars, min_buy_sell_ratio)
        if price_action:
            return watchlist_review_from_price_action(price_action), None
        trend = score_trend_candidate(code, name, quote, bars, min_buy_sell_ratio)
        if trend:
            return watchlist_review_from_trend(trend), None
        range_bound = score_range_bound(code, name, quote, bars, min_buy_sell_ratio)
        if range_bound:
            return watchlist_review_from_range_bound(range_bound), None
        return diagnose_watchlist_quote(code, name, quote, bars, min_buy_sell_ratio), None
    except Exception as exc:
        return None, f"自选股 {code} {name}: {exc}"


def scan_watchlist(
    watchlist_path: Path | None,
    workers: int,
    min_buy_sell_ratio: float,
) -> tuple[list[WatchlistReview], list[str], int]:
    items = load_watchlist(watchlist_path)
    if not items:
        return [], [], 0
    print(f"Loading {len(items)} watchlist stocks...", flush=True)
    quotes, errors = fetch_watchlist_quotes(items)
    reviews: list[WatchlistReview] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as executor:
        futures = [executor.submit(process_watchlist_quote, quote, min_buy_sell_ratio) for quote in quotes]
        for future in as_completed(futures):
            review, error = future.result()
            if review:
                reviews.append(review)
            if error:
                errors.append(error)
    status_order = {"符合策略": 5, "触发但谨慎": 4, "接近触发": 3, "策略二观察": 2, "趋势观察": 2, "未触发": 1, "数据不足": 0}
    reviews.sort(key=lambda item: (status_order.get(item.status, 0), item.final_score), reverse=True)
    enriched_reviews: list[WatchlistReview] = []
    near_context_budget = 12
    for review in reviews:
        if review.status == "接近触发" and near_context_budget > 0:
            enriched_reviews.append(enrich_watchlist_review_context(review))
            near_context_budget -= 1
        else:
            enriched_reviews.append(review)
    reviews = enriched_reviews
    reviews.sort(key=lambda item: (status_order.get(item.status, 0), item.final_score), reverse=True)
    return reviews, errors, len(items)


def scan_a_shares(
    top: int,
    workers: int,
    min_amount: float,
    min_buy_sell_ratio: float,
) -> tuple[list[AShareCandidate], list[TrendCandidate], list[RangeBoundCandidate], list[str], int]:
    print("Loading A-share universe...", flush=True)
    universe = load_universe(min_amount)
    print(f"Scanning {len(universe)} liquid main-board stocks...", flush=True)
    candidates: list[AShareCandidate] = []
    trend_candidates: list[TrendCandidate] = []
    range_bound_candidates: list[RangeBoundCandidate] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process_quote, quote, min_buy_sell_ratio) for quote in universe]
        for index, future in enumerate(as_completed(futures), 1):
            candidate, trend_candidate, wyckoff_candidate, error = future.result()
            if candidate:
                candidates.append(candidate)
            if trend_candidate:
                trend_candidates.append(trend_candidate)
            if wyckoff_candidate:
                range_bound_candidates.append(wyckoff_candidate)
            if error:
                errors.append(error)
            if index % 500 == 0:
                print(
                    f"  scanned {index}/{len(universe)}; triggers={len(candidates)}; trends={len(trend_candidates)}; strategy2={len(range_bound_candidates)}",
                    flush=True,
                )

    candidates.sort(
        key=lambda item: (
            price_action_rank_score(item),
            item.bullish_confidence,
            item.reward_risk,
            -item.false_breaks,
        ),
        reverse=True,
    )
    retrace_50_shortlist = [item for item in candidates if item.setup == "回踩50%"][:top]
    breakout_retest_shortlist = [item for item in candidates if item.setup == "突破后回踩"][:top]
    ma30_second_wave_shortlist = [item for item in candidates if item.setup == "二波回踩EMA20"][:top]
    other_shortlist = [
        item
        for item in candidates
        if item.setup not in {"回踩50%", "突破后回踩", "二波回踩EMA20", "突破EMA20"}
    ][:top]
    shortlist = retrace_50_shortlist + breakout_retest_shortlist + ma30_second_wave_shortlist + other_shortlist
    trend_candidates.sort(key=lambda item: item.final_score, reverse=True)
    trend_shortlist = [item for item in trend_candidates if item.code not in {candidate.code for candidate in shortlist}][:top]
    range_bound_candidates.sort(key=lambda item: item.final_score, reverse=True)
    range_bound_shortlist = [
        item
        for item in range_bound_candidates
        if item.code not in {candidate.code for candidate in shortlist}
    ][:top]
    print(
        f"Enriching {len(shortlist)} trigger candidates, {len(trend_shortlist)} trend candidates, and {len(range_bound_shortlist)} strategy2 candidates...",
        flush=True,
    )
    enriched = [enrich_candidate(candidate) for candidate in shortlist]
    enriched_trends = [enrich_trend_candidate(candidate) for candidate in trend_shortlist]
    enriched_range_bound = [enrich_range_bound_candidate(candidate) for candidate in range_bound_shortlist]
    enriched.sort(key=lambda item: item.final_score, reverse=True)
    enriched_trends.sort(key=lambda item: item.final_score, reverse=True)
    enriched_range_bound.sort(key=lambda item: item.final_score, reverse=True)
    return enriched, enriched_trends[:top], enriched_range_bound[:top], errors, len(universe)


def scan_t0_funds(
    top: int,
    workers: int,
    min_amount: float,
    min_buy_sell_ratio: float,
) -> tuple[list[T0FundCandidate], list[str], int]:
    print("Loading T+0 exchange-traded fund universe...", flush=True)
    try:
        universe = load_t0_fund_universe(min_amount)
    except Exception as exc:
        error = f"T+0基金观察池加载失败: {exc}"
        print(error, file=sys.stderr, flush=True)
        return [], [error], 0
    print(f"Scanning {len(universe)} liquid T+0 fund candidates...", flush=True)
    candidates: list[T0FundCandidate] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process_t0_fund_quote, quote, min_buy_sell_ratio) for quote in universe]
        for index, future in enumerate(as_completed(futures), 1):
            candidate, error = future.result()
            if candidate:
                candidates.append(candidate)
            if error:
                errors.append(error)
            if index % 200 == 0:
                print(f"  scanned {index}/{len(universe)} T+0 funds; triggers={len(candidates)}", flush=True)

    candidates.sort(
        key=lambda item: (
            price_action_rank_score(item.candidate),
            item.candidate.bullish_confidence,
            item.candidate.reward_risk,
            -item.candidate.false_breaks,
        ),
        reverse=True,
    )
    return candidates[:top], errors, len(universe)


def market_summary(indices: list[MarketIndex]) -> str:
    if not indices:
        return "大盘数据暂时不可用。"
    positives = sum(1 for item in indices if item.change_pct > 0)
    average_change = sum(item.change_pct for item in indices) / len(indices)
    mood = "偏多" if positives >= 3 and average_change > 0.4 else "震荡" if positives >= 2 else "偏弱"
    return f"指数环境：{mood}，跟踪指数平均涨幅 {average_change:+.2f}%。"


def section_label(candidate: AShareCandidate) -> str:
    industry = candidate.industry.name if candidate.industry else "n/a"
    concepts = candidate.concepts or []
    strong_concepts = sorted(concepts, key=lambda item: item.avg_change_pct if item.avg_change_pct is not None else -99, reverse=True)
    concept_names = "、".join(item.name for item in strong_concepts[:3]) if strong_concepts else "n/a"
    return f"{industry}；{concept_names}"


def section_strength_label(candidate: AShareCandidate) -> str:
    pieces: list[str] = []
    if candidate.industry:
        breadth = ""
        if candidate.industry.up_count is not None and candidate.industry.down_count is not None:
            breadth = f"({candidate.industry.up_count}涨/{candidate.industry.down_count}跌)"
        pieces.append(f"行业 {fmt_pct(candidate.industry.avg_change_pct)}{breadth}")
    if candidate.concepts:
        strongest = max(candidate.concepts, key=lambda item: item.avg_change_pct if item.avg_change_pct is not None else -99)
        breadth = ""
        if strongest.up_count is not None and strongest.down_count is not None:
            breadth = f"({strongest.up_count}涨/{strongest.down_count}跌)"
        pieces.append(f"最强概念 {strongest.name} {fmt_pct(strongest.avg_change_pct)}{breadth}")
    return "；".join(pieces) if pieces else "n/a"


def community_label(candidate: AShareCandidate | TrendCandidate | RangeBoundCandidate | WatchlistReview) -> str:
    community = candidate.community
    if not community:
        return "n/a"
    pieces = [
        f"{community.recent_posts}帖/24h",
        f"热度{community.discussion_score:.1f}",
        f"多{community.bullish_posts}/空{community.bearish_posts}",
    ]
    if community.lure_posts:
        pieces.append(f"疑诱{community.lure_posts}")
    return "；".join(pieces)


def range_bound_comment(candidate: RangeBoundCandidate) -> str:
    if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
        return f"有顶部/背离风险：{'、'.join(candidate.bearish_factors)}，只观察箱体承接。"
    if candidate.community and (candidate.community.hype_risk_score >= 3.5 or candidate.community.lure_posts >= 3):
        title = f"：{candidate.community.lure_titles[0]}" if candidate.community.lure_titles else ""
        return f"股吧疑似过热{title}；策略二只等下沿Pinbar后的右侧确认。"
    if candidate.bullish_confidence >= 75:
        return "接近震荡区间下沿并出现Al Brooks价格行为信号，可观察次日是否守住箱体下沿。"
    return "形态接近策略二，但置信度未达高门槛，继续等下沿承接确认。"


def trade_comment(candidate: AShareCandidate) -> str:
    if candidate.setup == "回踩50%":
        if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
            return f"顶部/背离风险：{'、'.join(candidate.bearish_factors)}，50%回调位支撑待确认，谨慎进场。"
        if candidate.bullish_confidence < 75:
            return "50%回调位结构成立但置信度未达高门槛，等待进一步确认信号。"
        return "50%回调位支撑有效，若后续跌破50%位或放量转弱按失败处理。"
    if candidate.setup == "二波回踩EMA20":
        if candidate.community and (candidate.community.hype_risk_score >= 3.5 or candidate.community.lure_posts >= 3):
            title = f"：{candidate.community.lure_titles[0]}" if candidate.community.lure_titles else ""
            return f"股吧疑似诱多/过热话术{title}，只作弱风险提示；二波结构重点看MA30上方承接能否延续。"
        if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
            return f"顶部/背离风险：{'、'.join(candidate.bearish_factors)}，二波结构谨慎进场。"
        if candidate.bullish_confidence < 75:
            return "二波回踩MA30结构成立但置信度未达高门槛，只观察不进场。"
        return "前段涨幅和MA30回踩结构较好，后续跌回MA30或放量转弱按二波失败处理。"
    if candidate.community and (candidate.community.hype_risk_score >= 3.5 or candidate.community.lure_posts >= 3):
        title = f"：{candidate.community.lure_titles[0]}" if candidate.community.lure_titles else ""
        return f"股吧疑似诱多/过热话术{title}，只作弱风险提示；只观察回踩位上方承接是否延续。"
    if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
        return f"顶部/背离风险：{'、'.join(candidate.bearish_factors)}，谨慎进场。"
    if candidate.bullish_confidence < 75:
        return "看涨置信度未达高置信门槛，只观察不进场。"
    if candidate.confidence_factors:
        return f"置信度加分：{'、'.join(candidate.confidence_factors)}；当前已回踩突破位附近，后续跌回突破位按假突破处理。"
    if candidate.false_breaks:
        return "有假突破痕迹，需等收盘重新站稳。"
    if candidate.buy_sell_ratio_60 >= 3 and candidate.gain_60 >= 20:
        return "60日买盘和趋势斜率都较强，重点看回踩位上方承接能否延续。"
    if candidate.reward_risk >= 2 and candidate.volume_ratio >= 1.5:
        return "形态和空间较好，当前重点是回踩支撑不破后的右侧延续。"
    if candidate.reward_risk < 1.2:
        return "盈亏比偏薄，只适合观察确认。"
    return "可观察突破位上方承接，跌回突破位按假突破处理。"


def range_bound_row(candidate: RangeBoundCandidate) -> str:
    confidence_reason = "、".join(candidate.confidence_factors) if candidate.confidence_factors else "-"
    bearish_reason = "、".join(candidate.bearish_factors) if candidate.bearish_factors else "-"
    return (
        f"| {candidate.code} | {candidate.name} | {fmt_price(candidate.close)} | {fmt_pct(candidate.pct_change)} | "
        f"{fmt_price(candidate.range_low)}-{fmt_price(candidate.range_high)} | {candidate.range_position:.0%} | "
        f"{fmt_pct(candidate.range_width_pct)} | {candidate.bullish_confidence:.0f}% | {confidence_reason} | "
        f"{candidate.bearish_confidence:.0f}% | {bearish_reason} | {section_label(candidate)} | {section_strength_label(candidate)} | "
        f"{community_label(candidate)} | {fmt_pct(candidate.gain_30)} | {fmt_pct(candidate.gain_60)} | "
        f"{candidate.buy_sell_ratio_60:.2f}x | {candidate.ma30:.2f}/{candidate.ma60:.2f} | {candidate.volume_ratio:.2f}x | "
        f"{range_bound_comment(candidate)} |"
    )


def candidate_row(candidate: AShareCandidate, fixed_rr: FixedStopRR | None = None) -> str:
    confidence_reason = "、".join(candidate.confidence_factors) if candidate.confidence_factors else "-"
    bearish_reason = "、".join(candidate.bearish_factors) if candidate.bearish_factors else "-"
    base = (
        f"| {candidate.code} | {candidate.name} | {fmt_price(candidate.close)} | {fmt_pct(candidate.pct_change)} | "
        f"{candidate.setup}/{'+'.join(candidate.signals)} | {section_label(candidate)} | {section_strength_label(candidate)} | "
        f"{community_label(candidate)} | "
        f"{candidate.bullish_confidence:.0f}% | {confidence_reason} | {candidate.bearish_confidence:.0f}% | {bearish_reason} | "
        f"{fmt_pct(candidate.velocity_30)}/日 | {fmt_pct(candidate.gain_60)} | {candidate.buy_sell_ratio_60:.2f}x | "
        f"{fmt_price(candidate.support)} | {fmt_price(candidate.stop)} | {fmt_price(candidate.target)} | "
        f"{candidate.reward_risk:.2f} | {fmt_confidence(candidate.reward_risk_confidence)} | {candidate.volume_ratio:.2f}x | {trade_comment(candidate)}"
    )
    if fixed_rr is not None:
        return f"{base} | {format_rr_markdown_row(fixed_rr)} |"
    return f"{base} |"


def append_candidate_table(
    lines: list[str],
    candidates: list[AShareCandidate],
    empty_note: str,
    fixed_rr_map: dict[str, FixedStopRR] | None = None,
) -> None:
    if fixed_rr_map is not None:
        lines.extend(
            [
                "| 代码 | 名称 | 收盘价 | 当日 | 形态 | 所属板块 | 板块强度 | 社区讨论 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 30日涨速 | 60日涨幅 | 60日买/卖 | 支撑/突破位 | 结构止损 | 上方压力 | 盈亏比 | 盈亏比置信度 | 量比 | 备注 | 固定5%止损RR |",
                "|---|---|---:|---:|---|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
            ]
        )
        if candidates:
            for candidate in candidates:
                lines.append(candidate_row(candidate, fixed_rr_map.get(candidate.code)))
        else:
            lines.append(f"| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | {empty_note} | - |")
        return
    lines.extend(
        [
            "| 代码 | 名称 | 收盘价 | 当日 | 形态 | 所属板块 | 板块强度 | 社区讨论 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 30日涨速 | 60日涨幅 | 60日买/卖 | 支撑/突破位 | 结构止损 | 上方压力 | 盈亏比 | 盈亏比置信度 | 量比 | 备注 |",
            "|---|---|---:|---:|---|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    if candidates:
        lines.extend(candidate_row(candidate) for candidate in candidates)
    else:
        lines.append(f"| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | {empty_note} |")


def compute_fixed_rr_for_candidates(
    candidates: list[AShareCandidate],
) -> dict[str, FixedStopRR]:
    """2026-06-04 裸 K 规则：对每个候选计算固定 5% 止损 RR。

    ``current_price`` = ``candidate.close``
    ``nearby_support`` = ``candidate.support``（突破位 / 结构止损上方）
    ``next_pressure`` = ``candidate.target``（结构上方的下一压力 / 量度目标）

    若 ``next_pressure`` 缺失（一般不会出现，因为 ``score_price_action`` 已保证）、
    ``reward <= 0`` 或 ``risk <= 0``，函数不会抛异常，对应 ``is_valid_long_candidate``
    为 ``False``，由 ``rank_best_long_candidates`` 在排序阶段过滤。
    """
    rr_map: dict[str, FixedStopRR] = {}
    for candidate in candidates:
        rr = compute_fixed_stop_rr(
            current_price=candidate.close,
            nearby_support=candidate.support,
            next_pressure=candidate.target,
            stop_pct=0.05,
        )
        rr_map[candidate.code] = rr
    return rr_map


def rank_candidates_by_fixed_rr(
    candidates: list[AShareCandidate],
    rr_map: dict[str, FixedStopRR],
) -> list[AShareCandidate]:
    """按 2026-06-04 规则综合排序（fixed RR > setup 质量 > final_score > bullish_confidence）。

    内部把 ``AShareCandidate`` 转成 ``dict`` 喂给 ``rank_best_long_candidates``，
    并把算出的 ``reward_to_risk`` 用 fixed 5% 版本的 RR 覆盖，
    以体现"只展示最优做多候选"的口径。
    """
    dicts: list[dict] = []
    for candidate in candidates:
        rr = rr_map.get(candidate.code)
        if rr is None or not rr.is_valid_long_candidate:
            continue
        dicts.append(
            {
                "code": candidate.code,
                "setup": candidate.setup,
                "reward_to_risk": rr.reward_to_risk,
                "final_score": candidate.final_score,
                "bullish_confidence": candidate.bullish_confidence,
                "_candidate": candidate,
            }
        )
    ranked = rank_best_long_candidates(dicts)
    return [item["_candidate"] for item in ranked]


def fixed_rr_best_long_section(
    candidates: list[AShareCandidate],
    rr_map: dict[str, FixedStopRR],
    top_n: int,
) -> list[str]:
    """渲染"最优做多候选（固定5%止损）"独立小节，供 Markdown 和 Lark 共用。"""
    ranked = rank_candidates_by_fixed_rr(candidates, rr_map)
    surfaced = ranked[:top_n]
    lines: list[str] = [
        "",
        "## 最优做多候选（固定5%止损 · 2026-06-04 规则）",
        "",
        f"按 fixed 5% 止损 + reward_to_risk + setup 质量综合排序，只 surfaced best long candidates；"
        f"next_pressure 缺失 / reward<=0 / risk<=0 一律过滤。当前上榜 {len(surfaced)} / 全候选 {len(candidates)}。",
        "",
        "| 代码 | 名称 | 收盘价 | 形态 | 支撑/突破位 | 上方压力 | 5%止损 | 风险 | 空间 | RR | 看涨置信度 | final_score | 备注 |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    if surfaced:
        for candidate in surfaced:
            rr = rr_map[candidate.code]
            lines.append(
                f"| {candidate.code} | {candidate.name} | {fmt_price(candidate.close)} | "
                f"{candidate.setup} | {fmt_price(candidate.support)} | {fmt_price(candidate.target)} | "
                f"{fmt_price(rr.stop_price)} | {fmt_price(rr.risk)} | {fmt_price(rr.reward)} | "
                f"{rr.reward_to_risk:.2f} | {candidate.bullish_confidence:.0f}% | {candidate.final_score:.1f} | "
                f"{trade_comment(candidate)} |"
            )
    else:
        lines.append(f"| - | - | - | - | - | - | - | - | - | - | - | - | 今天无满足固定5%止损 RR 规则的最优做多候选。 |")
    return lines


def fixed_rr_best_long_lark(
    candidates: list[AShareCandidate],
    rr_map: dict[str, FixedStopRR],
    top_n: int,
) -> str:
    """渲染 Lark/飞书消息单行汇总（与 Markdown 共用同一排序）。"""
    ranked = rank_candidates_by_fixed_rr(candidates, rr_map)
    surfaced = ranked[:top_n]
    if not surfaced:
        return "2026-06-04 固定5%止损 RR: 今天无满足规则的最优做多候选。"
    pieces: list[str] = ["【固定5%止损 RR · 最优做多候选】"]
    for index, candidate in enumerate(surfaced, 1):
        rr = rr_map[candidate.code]
        pieces.append(
            f"{index}. {candidate.code} {candidate.name} 收{fmt_price(candidate.close)} "
            f"形态={candidate.setup} {format_rr_lark_line(rr)} 看涨{candidate.bullish_confidence:.0f}% "
            f"final={candidate.final_score:.1f}"
        )
    return "\n".join(pieces)


def t0_fund_comment(item: T0FundCandidate) -> str:
    candidate = item.candidate
    if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
        return f"顶部/背离风险：{'、'.join(candidate.bearish_factors)}；T+0只适合小仓观察，不追高。"
    if candidate.bullish_confidence < 75:
        return "按策略一回踩触发但置信度未达高门槛，只作为板块热度观察。"
    if candidate.reward_risk < 1.2:
        return "空间偏薄，优先观察板块强弱，不适合追信号日。"
    return "按策略一回踩触发；若用于T+0观察，仍以券商T+0资格和盘中承接为准。"


def t0_fund_row(item: T0FundCandidate) -> str:
    candidate = item.candidate
    confidence_reason = "、".join(candidate.confidence_factors) if candidate.confidence_factors else "-"
    bearish_reason = "、".join(candidate.bearish_factors) if candidate.bearish_factors else "-"
    return (
        f"| {candidate.code} | {candidate.name} | {item.t0_reason} | {fmt_price(candidate.close)} | "
        f"{fmt_pct(candidate.pct_change)} | {candidate.setup}/{'+'.join(candidate.signals)} | "
        f"{candidate.bullish_confidence:.0f}% | {confidence_reason} | {candidate.bearish_confidence:.0f}% | {bearish_reason} | "
        f"{fmt_pct(candidate.velocity_30)}/日 | {fmt_pct(candidate.gain_60)} | {candidate.buy_sell_ratio_60:.2f}x | "
        f"{fmt_price(candidate.support)} | {fmt_price(candidate.stop)} | {fmt_price(candidate.target)} | "
        f"{candidate.reward_risk:.2f} | {fmt_confidence(candidate.reward_risk_confidence)} | {candidate.volume_ratio:.2f}x | {t0_fund_comment(item)} |"
    )


def trend_comment(candidate: TrendCandidate) -> str:
    if candidate.community and (candidate.community.hype_risk_score >= 3.5 or candidate.community.lure_posts >= 3):
        title = f"：{candidate.community.lure_titles[0]}" if candidate.community.lure_titles else ""
        return f"股吧疑似诱多/过热话术{title}，等待更扎实的技术触发。"
    if candidate.bearish_confidence >= 55 and candidate.bearish_factors:
        return f"顶部/背离风险：{'、'.join(candidate.bearish_factors)}。"
    if candidate.bullish_confidence < 75:
        return "看涨置信度未达高置信门槛，等待更强触发。"
    if candidate.confidence_factors:
        return f"置信度加分：{'、'.join(candidate.confidence_factors)}。"
    if candidate.buy_sell_ratio_60 >= 3 and candidate.gain_60 >= 60:
        return "趋势很强，等待低吸或下一次突破确认。"
    if candidate.velocity_30 > 1.5:
        return "30日涨速较快，避免连续加速后追高。"
    return "趋势质量达标，等待裸K触发点。"


def trend_row(candidate: TrendCandidate) -> str:
    confidence_reason = "、".join(candidate.confidence_factors) if candidate.confidence_factors else "-"
    bearish_reason = "、".join(candidate.bearish_factors) if candidate.bearish_factors else "-"
    return (
        f"| {candidate.code} | {candidate.name} | {fmt_price(candidate.close)} | {fmt_pct(candidate.pct_change)} | "
        f"{section_label(candidate)} | {section_strength_label(candidate)} | {community_label(candidate)} | "
        f"{candidate.bullish_confidence:.0f}% | {confidence_reason} | {candidate.bearish_confidence:.0f}% | {bearish_reason} | "
        f"{fmt_pct(candidate.gain_30)} | {fmt_pct(candidate.velocity_30)}/日 | {fmt_pct(candidate.gain_60)} | "
        f"{candidate.buy_sell_ratio_60:.2f}x | {candidate.ma5:.2f}/{candidate.ma10:.2f}/{candidate.ma20:.2f}/{candidate.ma30:.2f}/{candidate.ma60:.2f} | "
        f"{candidate.volume_ratio:.2f}x | {trend_comment(candidate)} |"
    )


def fmt_optional_pct(value: float | None) -> str:
    return fmt_pct(value) if value is not None else "n/a"


def fmt_optional_price(value: float | None) -> str:
    return fmt_price(value) if value is not None else "n/a"


def watchlist_row(review: WatchlistReview) -> str:
    confidence_reason = "、".join(review.confidence_factors) if review.confidence_factors else "-"
    bearish_reason = "、".join(review.bearish_factors) if review.bearish_factors else "-"
    signal = "+".join(review.signals) if review.signals else "-"
    confidence = f"{review.bullish_confidence:.0f}%" if review.bullish_confidence is not None else "n/a"
    bearish = f"{review.bearish_confidence:.0f}%" if review.bearish_confidence is not None else "n/a"
    volume_ratio = f"{review.volume_ratio:.2f}x" if review.volume_ratio is not None else "n/a"
    buy_sell = f"{review.buy_sell_ratio_60:.2f}x" if review.buy_sell_ratio_60 is not None else "n/a"
    rr = f"{review.reward_risk:.2f}" if review.reward_risk is not None else "n/a"
    rr_confidence = fmt_confidence(review.reward_risk_confidence)
    ma = (
        f"{review.ma5:.2f}/{review.ma10:.2f}/{review.ma20:.2f}/{review.ma30:.2f}/{review.ma60:.2f}"
        if (
            review.ma5 is not None
            and review.ma10 is not None
            and review.ma20 is not None
            and review.ma30 is not None
            and review.ma60 is not None
        )
        else "n/a"
    )
    return (
        f"| {review.code} | {review.name} | {fmt_price(review.close)} | {fmt_pct(review.pct_change)} | "
        f"{review.status} | {review.setup}/{signal} | {confidence} | {confidence_reason} | {bearish} | {bearish_reason} | "
        f"{section_label(review)} | {section_strength_label(review)} | {community_label(review)} | "
        f"{fmt_optional_pct(review.gain_30)} | {fmt_optional_pct(review.gain_60)} | {buy_sell} | "
        f"{ma} | {fmt_optional_price(review.support)} | {fmt_optional_price(review.target)} | {rr} | {rr_confidence} | {volume_ratio} | {review.comment} |"
    )


def build_report(
    candidates: list[AShareCandidate],
    trend_candidates: list[TrendCandidate],
    range_bound_candidates: list[RangeBoundCandidate],
    t0_fund_candidates: list[T0FundCandidate],
    watchlist_reviews: list[WatchlistReview],
    errors: list[str],
    universe_size: int,
    t0_fund_universe_size: int,
    watchlist_size: int,
    indices: list[MarketIndex],
    min_amount: float,
    min_buy_sell_ratio: float,
    t0_fund_min_amount: float,
    fixed_rr_top_n: int = 5,
) -> str:
    # 2026-06-04 裸 K RR 规则：先算 fixed 5% RR，再按综合排序取 top N
    fixed_rr_map = compute_fixed_rr_for_candidates(candidates)
    fixed_rr_ranked = rank_candidates_by_fixed_rr(candidates, fixed_rr_map)
    fixed_rr_surfaced = fixed_rr_ranked[:fixed_rr_top_n]
    displayed_long_candidates = fixed_rr_surfaced
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    lines = [
        "# 每日A股裸K做多观察报告",
        "",
        f"Generated: {now:%Y-%m-%d %H:%M %Z}",
        "",
        "这是技术形态和公开行情研究，不是投资建议，也不是自动交易信号。",
        "",
        "## 大盘环境",
        "",
        market_summary(indices),
        "",
        "| 指数 | 点位 | 涨跌幅 | 成交额 |",
        "|---|---:|---:|---:|",
    ]
    if indices:
        lines.extend(f"| {item.name} | {item.price:.2f} | {fmt_pct(item.change_pct)} | {fmt_amount(item.amount)} |" for item in indices)
    else:
        lines.append("| - | - | - | - |")

    lines.extend(["", "## 快速结论", ""])
    if fixed_rr_surfaced:
        top = fixed_rr_surfaced[0]
        top_rr = fixed_rr_map[top.code]
        lines.append(
            f"- 综合最值得观察（按固定5%止损 RR · 2026-06-04 规则）：{top.code} {top.name}，{top.setup}，"
            f"5%止损 {fmt_price(top_rr.stop_price)}（风险 {fmt_price(top_rr.risk)}），"
            f"下一压力 {fmt_price(top_rr.next_pressure)}（空间 {fmt_price(top_rr.reward)}），"
            f"RR {top_rr.reward_to_risk:.2f}，板块：{section_label(top)}。"
        )
        if fixed_rr_surfaced[1:]:
            names = "、".join(
                f"{c.code} {c.name} RR={fixed_rr_map[c.code].reward_to_risk:.2f}"
                for c in fixed_rr_surfaced[1:]
            )
            lines.append(f"- 其余上榜（fixed RR 排序）：{names}。")
        lines.append("- 执行口径：按 2026-06-04 裸 K 规则筛选；stop = current * 0.95；missing/无效 next_pressure 一律过滤；只 surfaced best long candidates。")
    elif candidates:
        lines.append(
            "- 今天有原策略候选，但没有满足固定5%止损 RR / 下一压力有效性的最优做多候选；"
            "按 2026-06-04 规则不 surfaced 做多候选。"
        )
        lines.append("- 执行口径：stop = current * 0.95；missing/无效 next_pressure、reward<=0、risk<=0 一律过滤。")
    elif trend_candidates:
        top_trend = trend_candidates[0]
        lines.append(
            f"- 今天没有严格触发建仓形态的票；趋势良好池第一名：{top_trend.code} {top_trend.name}，"
            f"60日涨幅 {fmt_pct(top_trend.gain_60)}，60日买卖盘 {top_trend.buy_sell_ratio_60:.2f}x。"
        )
        lines.append("- 这些票只作为趋势观察池，仍需等待突破、回踩不破或明确多头K线信号。")
    else:
        lines.append("- 今天没有同时满足突破后回踩不破/二波回踩MA30、均线发散、K线确认和基础盈亏比的主板股票。")
    if watchlist_reviews:
        actionable = [item for item in watchlist_reviews if item.status in {"符合策略", "触发但谨慎"}]
        near = [item for item in watchlist_reviews if item.status == "接近触发"]
        strategy2 = [item for item in watchlist_reviews if item.status == "策略二观察"]
        if actionable:
            names = "、".join(f"{item.code} {item.name}" for item in actionable[:5])
            lines.append(f"- 自选股中当前触发策略检查：{names}。口径为突破后回踩不破或二波回踩MA30，不追突破信号日。")
        elif near:
            names = "、".join(f"{item.code} {item.name}" for item in near[:5])
            lines.append(f"- 自选股暂无严格买点；接近触发的有：{names}。")
        else:
            lines.append("- 自选股暂无严格买点，主要等待突破后回踩不破、二波回踩MA30和看涨K线确认。")
        if strategy2:
            top_s2 = strategy2[0]
            if top_s2.support is not None and top_s2.target is not None and top_s2.target > top_s2.support:
                range_pos = (top_s2.close - top_s2.support) / (top_s2.target - top_s2.support)
            else:
                range_pos = None
            pos_str = f"箱体位置 {range_pos:.0%}" if range_pos is not None else ""
            lines.append(
                f"- 自选股策略二再吸筹观察：{top_s2.code} {top_s2.name}，"
                f"{pos_str}，看涨置信度 {top_s2.bullish_confidence:.0f}%。"
            )
    if range_bound_candidates:
        top_strategy2 = range_bound_candidates[0]
        lines.append(
            f"- 策略二再吸筹观察：{top_strategy2.code} {top_strategy2.name}，"
            f"箱体位置 {top_strategy2.range_position:.0%}，看涨置信度 {top_strategy2.bullish_confidence:.0f}%。"
        )
    if t0_fund_candidates:
        top_t0 = t0_fund_candidates[0].candidate
        lines.append(
            f"- 板块T+0基金观察：{top_t0.code} {top_t0.name}，"
            f"{top_t0.setup}，看涨置信度 {top_t0.bullish_confidence:.0f}%，只作可交易基金观察。"
        )

    retrace_50_candidates = [candidate for candidate in displayed_long_candidates if candidate.setup == "回踩50%"]
    breakout_retest_candidates = [candidate for candidate in displayed_long_candidates if candidate.setup == "突破后回踩"]
    ma30_second_wave_candidates = [candidate for candidate in displayed_long_candidates if candidate.setup == "二波回踩EMA20"]

    ema20_breakout_candidates = [candidate for candidate in displayed_long_candidates if candidate.setup == "突破EMA20"]
    channel_breakout_candidates = [candidate for candidate in displayed_long_candidates if candidate.setup == "下降通道突破"]
    other_candidates = [
        candidate
        for candidate in displayed_long_candidates
        if candidate.setup not in {"回踩50%", "突破后回踩", "二波回踩EMA20", "突破EMA20", "下降通道突破"}
    ]
    lines.extend(
        [
            "",
            "## 做多候选",
            "",
            "### 做多候选A：回踩 50% 回调位（策略一优先）",
            "",
        ]
    )
    append_candidate_table(
        lines,
        retrace_50_candidates,
        "今天未筛出回踩 50% 回调位的严格候选。",
        fixed_rr_map=fixed_rr_map,
    )
    lines.extend(
        [
            "",
            "### 做多候选B：突破后回踩前压力位",
            "",
        ]
    )
    append_candidate_table(
        lines,
        breakout_retest_candidates,
        "今天未筛出突破后回踩前压力位的严格候选。",
        fixed_rr_map=fixed_rr_map,
    )
    lines.extend(
        [
            "",
            "### 做多候选C：强趋势二波回踩EMA20",
            "",
        ]
    )
    append_candidate_table(
        lines,
        ma30_second_wave_candidates,
        "今天未筛出强趋势二波回踩EMA20的严格候选。",
        fixed_rr_map=fixed_rr_map,
    )
    lines.extend(
        [
            "",
            "### 做多候选D：下降通道涨停突破（趋势早期信号）",
            "",
            "涨停大阳线（+10%）突破持续20根K线以上的下降通道上轨，且收盘站上EMA20。这是趋势可能反转的早期信号，",
            "胜在进场位置低（趋势刚启动），但假突破风险较高——只作启动观察，不等同于回踩确认后的做多候选A/B/C。",
            "执行口径：不追涨停信号日，等次日是否有回踩通道上轨不破或缩量整理的右侧确认。",
            "",
        ]
    )
    append_candidate_table(
        lines,
        channel_breakout_candidates,
        "今天未筛出下降通道涨停突破的趋势早期候选。",
        fixed_rr_map=fixed_rr_map,
    )
    if other_candidates:
        lines.extend(
            [
                "",
                "### 做多候选补充：其他严格形态",
                "",
            ]
        )
        append_candidate_table(lines, other_candidates, "今天未筛出其他严格形态。", fixed_rr_map=fixed_rr_map)

    # 2026-06-04 裸 K RR 规则：独立小节，按 fixed 5% RR 综合排序 surfaced best long candidates
    lines.extend(fixed_rr_best_long_section(candidates, fixed_rr_map, fixed_rr_top_n))

    lines.extend(
        [
            "",
            "## 趋势良好观察池",
            "",
            "这些股票满足 30日上涨、60日上涨、MA5/10/20 多头、且60日买卖盘强度达标；但不一定已经触发突破建仓信号。",
            "",
            "| 代码 | 名称 | 收盘价 | 当日 | 所属板块 | 板块强度 | 社区讨论 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 30日涨幅 | 30日涨速 | 60日涨幅 | 60日买/卖 | MA5/10/20/30/60 | 量比 | 备注 |",
            "|---|---|---:|---:|---|---|---|---:|---|---:|---|---:|---:|---:|---:|---|---:|---|",
        ]
    )
    if trend_candidates:
        lines.extend(trend_row(candidate) for candidate in trend_candidates)
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## 板块T+0基金观察",
            "",
            "从公开行情中抓取疑似支持T+0的场内基金（跨境/QDII、商品、债券、货币等名称关键词推断），按策略一裸K突破规则筛选。T+0资格最终以券商交易规则为准。这里用于观察可日内处理的板块/主题载体，不等同于A股个股候选。",
            "",
            "| 代码 | 名称 | T+0口径 | 收盘价 | 当日 | 形态 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 30日涨速 | 60日涨幅 | 60日买/卖 | 支撑/突破位 | 结构止损 | 上方压力 | 盈亏比 | 盈亏比置信度 | 量比 | 备注 |",
            "|---|---|---|---:|---:|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    if t0_fund_candidates:
        lines.extend(t0_fund_row(candidate) for candidate in t0_fund_candidates)
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | 今天未筛出按策略一触发的T+0基金。 |")

    lines.extend(
        [
            "",
            "## 策略二：震荡区间选股观察",
            "",
            f"寻找一段上涨后进入近{STRATEGY2_BOX_DAYS}日横盘箱体、当前接近箱体下沿且出现看涨Pinbar的股票。这里是区间下沿承接观察策略，不等同于策略一的突破建仓信号。",
            "",
            "| 代码 | 名称 | 收盘价 | 当日 | 横盘箱体 | 箱体位置 | 前段涨幅 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 所属板块 | 板块强度 | 社区讨论 | 30日涨幅 | 60日涨幅 | 60日买/卖 | MA30/60 | 量比 | 备注 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|---|---|---:|---:|---:|---|---:|---|",
        ]
    )
    if range_bound_candidates:
        lines.extend(range_bound_row(candidate) for candidate in range_bound_candidates)
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | 今天未筛出符合策略二的主板股票。 |")

    lines.extend(
        [
            "",
            "## 自选股策略检查",
            "",
            f"来自 `a_share_watchlist.txt`，共 {watchlist_size} 只；这里不改变主板日报股票池，只额外检查你的自选列表是否触发同一套买入策略。",
            "",
            "| 代码 | 名称 | 收盘价 | 当日 | 状态 | 形态/信号 | 看涨置信度 | 看涨因子 | 看跌风险 | 看跌因子 | 所属板块 | 板块强度 | 社区讨论 | 30日涨幅 | 60日涨幅 | 60日买/卖 | MA5/10/20/30/60 | 支撑/突破位 | 上方压力 | 盈亏比 | 盈亏比置信度 | 量比 | 备注 |",
            "|---|---|---:|---:|---|---|---:|---|---:|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    if watchlist_reviews:
        priority = [item for item in watchlist_reviews if item.status in {"符合策略", "触发但谨慎", "接近触发", "策略二观察", "趋势观察"}]
        others = [item for item in watchlist_reviews if item not in priority]
        displayed = priority[:24] + others[: max(0, 32 - len(priority[:24]))]
        lines.extend(watchlist_row(review) for review in displayed)
        hidden = len(watchlist_reviews) - len(displayed)
        if hidden > 0:
            lines.append(f"| ... | ... | ... | ... | 未展示 | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | 另有 {hidden} 只未触发股票未展示。 |")
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | 未配置或未取到自选股。 |")

    if displayed_long_candidates:
        lines.extend(["", "## 消息面摘录", ""])
        for candidate in displayed_long_candidates:
            note = candidate.latest_note or "未抓到明确最新公告摘要，按技术和板块强度观察。"
            lines.append(f"- {candidate.code} {candidate.name}：{note}")
    if trend_candidates:
        if not candidates:
            lines.extend(["", "## 消息面摘录", ""])
        for candidate in trend_candidates:
            note = candidate.latest_note or "未抓到明确最新公告摘要，按趋势和板块强度观察。"
            lines.append(f"- {candidate.code} {candidate.name}：{note}")

    lines.extend(
        [
            "",
            "## 方法",
            "",
            (
                f"股票池为沪深主板、非ST、非新股、成交额不低于 {fmt_amount(min_amount)} 的股票。"
                "技术筛选要求：策略一A为近12日已收盘突破前30日压力位，当前回踩前压力位附近并站稳；"
                "策略一B为前段涨幅不低于40%，从前高回撤约12%-55%，最近回踩MA30不破后出现二波启动；"
                "策略一D为涨停大阳线（+10%）突破持续20根K线以上的下降通道上轨且收盘站上EMA20，"
                "作为趋势早期启动信号，不追信号日，等次日回踩通道上轨不破或缩量整理确认；"
                "均线硬门槛为MA20和MA30上行，且收盘价在MA20上方；"
                "出现吞没、启明星、回踩不破、MA30回踩不破或二波启动，且结构止损风险不超过5%、盈亏比大于1.00。"
            ),
            "盈亏比 = (上方压力位或保守量度目标 - 当前收盘价) / (当前收盘价 - 结构止损位)。结构止损通常取回踩低点或二波启动前低点下方；若结构风险超过5%，不纳入严格候选。",
            "盈亏比置信度表示交易空间质量，不是上涨概率：RR约1.0为50%，1.5约65%，2.0约78%，3.0及以上约90%+；数值越高代表收益空间相对结构风险越充足。",
            (
                "30日涨速、60日涨幅和60日买卖盘强度只作为趋势质量字段展示，不作为做多候选建仓硬条件；"
                f"趋势观察池要求60日买卖盘强度不低于 {min_buy_sell_ratio:.2f}x。"
            ),
            "突破压力位时参考成交量；放量突破会提高后市看涨置信度，并在置信因子中标注。",
            "参考Al Brooks价格行为口径新增右侧确认层：强趋势背景加分；重叠K较多、交易区间上沿首次突破、突破K收盘偏弱、突破后跌回压力位会提高看跌风险；突破后站稳多日、二次回踩不破，或回踩阶段形成三推楔形牛旗并出现看涨反转K线，会提高看涨置信度。",
            "回踩不破要求当日触及突破位附近后收在其上方，并收阳或形成看涨Pinbar；单纯大阴线守住旧压力位不算严格信号。",
            "二波回踩EMA20要求前段已有显著涨幅，回撤触及MA30附近后未有效跌破，当前收阳并放量或重新突破短线高点。",
            "执行口径调整为：突破信号日不纳入严格做多候选，只在后续回踩突破位附近或MA30附近且不破位时纳入。",
            "板块强度纳入置信度和排序：强行业/强概念共振加分，弱板块增加看跌风险；但不作为建仓硬条件。",
            "东方财富股吧讨论度纳入低权重情绪因子：讨论活跃且情绪不过热时小幅加分；疑似诱多/过热话术只作为弱风险提示和小幅扣分，不作为强信号。",
            "自选股检查复用同一套策略标准（策略一突破回踩/二波回踩EMA20 + 策略二震荡区间下沿）；若出现“符合策略/触发但谨慎”，表示已出现突破后回踩不破或二波回踩EMA20结构。",
            (
                f"板块T+0基金观察池来自东方财富场内基金行情，成交额不低于 {fmt_amount(t0_fund_min_amount)}；"
                "用名称关键词粗筛跨境/QDII、商品、债券、货币等通常支持T+0的ETF/LOF，再复用策略一筛选。"
                "T+0资格和交易限制以券商端最终规则为准。"
            ),
            f"策略二筛选震荡区间选股观察形态：前段有明显上涨，随后形成近{STRATEGY2_BOX_DAYS}日横盘箱体，当前靠近箱体下沿并出现看涨Pinbar；它是下沿承接观察，不替代策略一突破进场规则。",
            "股价放量上穿30日线，或回踩30日线不破并出现Pinbar时，只提高看涨置信度，不作为入选硬条件。",
            "回测归因只作为研究观察项；单次归因调权若不能提升滚动回测表现，不固化到策略权重。",
            "MACD底背离提高看涨置信度；MACD顶背离、M字顶、多重顶提高看跌风险并压低看涨排序。",
            "默认看涨置信度达到75%及以上才视为高置信；只有看涨置信度高、且看跌风险可控时，才更接近进场观察条件。",
            "60日买卖盘强度使用日K代理口径：60日阳线成交量 / 60日阴线成交量。",
            (
                "2026-06-04 裸 K risk/reward 规则：`stop_price = current_price * 0.95`，"
                "`next_pressure` 缺失 / `reward <= 0` / `risk <= 0` 一律不作为做多候选展示；"
                "按 `reward_to_risk` → 裸 K setup 质量（突破后回踩 / 二波回踩EMA20）→ final_score → 看涨置信度 综合排序，"
                "日报只 surfaced best long candidates（前 5）。"
                "本规则用于独立『最优做多候选』小节，不替换原结构止损 / 形态分组的视图。"
            ),
            f"本次有效股票池：{universe_size} 只；T+0基金观察池：{t0_fund_universe_size} 只；数据错误：{len(errors)} 条。",
        ]
    )
    if errors:
        lines.extend(["", "## 数据备注", ""])
        lines.extend(f"- {error}" for error in errors[:8])
        if len(errors) > 8:
            lines.append(f"- 另有 {len(errors) - 8} 条数据错误未展示。")

    return "\n".join(lines) + "\n"


def write_report(report: str, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d")
    dated_path = report_dir / f"a_share_daily_{stamp}.md"
    latest_path = report_dir / "a_share_latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    return dated_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an A-share price-action long watchlist report.")
    parser.add_argument("--top", type=int, default=8, help="Number of A-share long candidates to show.")
    parser.add_argument("--workers", type=int, default=20, help="Concurrent Sina K-line requests.")
    parser.add_argument("--min-amount", type=float, default=80_000_000, help="Minimum daily amount in CNY.")
    parser.add_argument(
        "--min-buy-sell-ratio",
        type=float,
        default=2.0,
        help="Minimum 60-day up-candle volume / down-candle volume ratio.",
    )
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR, help="Directory for Markdown reports.")
    parser.add_argument(
        "--watchlist",
        default="a_share_watchlist.txt",
        help="Optional user watchlist file. Each line can contain a 6-digit code and optional name.",
    )
    parser.add_argument(
        "--t0-fund-min-amount",
        type=float,
        default=DEFAULT_T0_FUND_MIN_AMOUNT,
        help="Minimum daily amount in CNY for T+0 exchange-traded fund observation.",
    )
    args = parser.parse_args()

    try:
        indices = parse_hq_indices()
        candidates, trend_candidates, range_bound_candidates, errors, universe_size = scan_a_shares(
            args.top,
            args.workers,
            args.min_amount,
            args.min_buy_sell_ratio,
        )
        t0_fund_candidates, t0_fund_errors, t0_fund_universe_size = scan_t0_funds(
            args.top,
            args.workers,
            args.t0_fund_min_amount,
            args.min_buy_sell_ratio,
        )
        errors.extend(t0_fund_errors)
        watchlist_reviews, watchlist_errors, watchlist_size = scan_watchlist(
            Path(args.watchlist) if args.watchlist else None,
            args.workers,
            args.min_buy_sell_ratio,
        )
        errors.extend(watchlist_errors)
        report = build_report(
            candidates,
            trend_candidates,
            range_bound_candidates,
            t0_fund_candidates,
            watchlist_reviews,
            errors,
            universe_size,
            t0_fund_universe_size,
            watchlist_size,
            indices,
            args.min_amount,
            args.min_buy_sell_ratio,
            args.t0_fund_min_amount,
        )
        report_path = write_report(report, Path(args.report_dir))
        print(f"Wrote {report_path}")
        if candidates:
            top = candidates[0]
            print(f"Top A-share long: {top.code} {top.name} rr={top.reward_risk:.2f} score={top.final_score:.1f}")
        elif trend_candidates:
            top = trend_candidates[0]
            print(
                f"Top A-share trend: {top.code} {top.name} "
                f"gain60={top.gain_60:.2f}% buy/sell={top.buy_sell_ratio_60:.2f}x"
            )
        return 0
    except Exception as exc:
        print(f"a_share_daily_agent failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
