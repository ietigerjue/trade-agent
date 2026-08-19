#!/usr/bin/env python3
"""US stock market data adapter.

Fetches daily OHLCV bars from Yahoo Finance's public v8 chart API and
converts them into the `Bar` format used by the A-share strategy functions.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from trading_strategy import Bar

# ── Yahoo Finance v8 chart API ──────────────────────────────────────────
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# ── Default US stock universe (85 liquid large/mid-cap stocks) ──────────
DEFAULT_US_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA",
    "BRK-B", "JPM", "LLY", "V", "MA", "UNH", "XOM", "COST",
    "NFLX", "WMT", "ORCL", "HD", "PG", "JNJ", "BAC", "ABBV",
    "KO", "PLTR", "AMD", "CRM", "CSCO", "CVX", "WFC", "IBM",
    "GE", "MRK", "AXP", "NOW", "MCD", "DIS", "INTU", "GS",
    "UBER", "CAT", "QCOM", "TXN", "VZ", "T", "AMAT", "SPGI",
    "BKNG", "ISRG", "PFE", "BA", "UNP", "LOW", "RTX", "HON",
    "ADBE", "PANW", "LRCX", "AMGN", "MU", "DE", "NKE", "SBUX",
    "COIN", "MSTR", "SMCI", "ARM", "SHOP", "CRWD", "SNOW",
    "NET", "DDOG", "MDB", "RBLX", "ROKU", "SOFI", "HOOD",
    "UPST",
]

US_STOCK_NAMES: dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
    "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta Platforms",
    "AVGO": "Broadcom", "TSLA": "Tesla", "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase", "LLY": "Eli Lilly", "V": "Visa",
    "MA": "Mastercard", "UNH": "UnitedHealth", "XOM": "Exxon Mobil",
    "COST": "Costco", "NFLX": "Netflix", "WMT": "Walmart",
    "ORCL": "Oracle", "HD": "Home Depot", "PG": "Procter & Gamble",
    "JNJ": "Johnson & Johnson", "BAC": "Bank of America", "ABBV": "AbbVie",
    "KO": "Coca-Cola", "PLTR": "Palantir", "AMD": "Advanced Micro Devices",
    "CRM": "Salesforce", "CSCO": "Cisco", "CVX": "Chevron",
    "WFC": "Wells Fargo", "IBM": "IBM", "GE": "GE Aerospace",
    "MRK": "Merck", "AXP": "American Express", "NOW": "ServiceNow",
    "MCD": "McDonald's", "DIS": "Disney", "INTU": "Intuit",
    "GS": "Goldman Sachs", "UBER": "Uber", "CAT": "Caterpillar",
    "QCOM": "Qualcomm", "TXN": "Texas Instruments", "VZ": "Verizon",
    "T": "AT&T", "AMAT": "Applied Materials", "SPGI": "S&P Global",
    "BKNG": "Booking Holdings", "ISRG": "Intuitive Surgical",
    "PFE": "Pfizer", "BA": "Boeing", "UNP": "Union Pacific",
    "LOW": "Lowe's", "RTX": "RTX", "HON": "Honeywell",
    "ADBE": "Adobe", "PANW": "Palo Alto Networks", "LRCX": "Lam Research",
    "AMGN": "Amgen", "MU": "Micron", "DE": "Deere",
    "NKE": "Nike", "SBUX": "Starbucks", "COIN": "Coinbase",
    "MSTR": "MicroStrategy", "SMCI": "Super Micro Computer",
    "ARM": "Arm Holdings", "SHOP": "Shopify", "CRWD": "CrowdStrike",
    "SNOW": "Snowflake", "NET": "Cloudflare", "DDOG": "Datadog",
    "MDB": "MongoDB", "RBLX": "Roblox", "ROKU": "Roku",
    "SOFI": "SoFi", "HOOD": "Robinhood", "UPST": "Upstart",
}

# Minimum bars required for strategy functions (130 ≈ 6 months trading days)
_MIN_BARS = 120


# ── HTTP helpers ────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: int = 30) -> Any:
    """Fetch JSON from a URL with retries and browser-like headers."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_exc}")


# ── Data fetching ───────────────────────────────────────────────────────

def fetch_us_daily_bars(symbol: str) -> list[Bar]:
    """Fetch daily OHLCV bars from Yahoo Finance v8 chart API.

    Requests ~6 months of daily data. Converts timestamps to YYYY-MM-DD
    date strings and wraps each bar in a `Bar` dataclass.

    Raises RuntimeError if fewer than {_MIN_BARS} bars are returned.
    """
    # Yahoo Finance ticker encoding: BRK-B stays as-is in the URL
    encoded = urllib.parse.quote(symbol)
    url = (
        f"{YAHOO_CHART_URL.format(symbol=encoded)}"
        f"?range=1y&interval=1d&includePrePost=false"
    )
    data = _fetch_json(url, timeout=30)

    chart = data.get("chart", {}).get("result", [])
    if not chart:
        raise RuntimeError(f"No chart data for {symbol}")
    result = chart[0]
    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    opens = quotes.get("open", [])
    highs = quotes.get("high", [])
    lows = quotes.get("low", [])
    closes = quotes.get("close", [])
    volumes = quotes.get("volume", [])

    bars: list[Bar] = []
    for i, ts in enumerate(timestamps):
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = volumes[i]
        if o is None or h is None or l is None or c is None:
            continue  # skip null bars (holidays, gaps)
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
        bars.append(Bar(date=date_str, open=float(o), high=float(h),
                        low=float(l), close=float(c),
                        volume=float(v) if v is not None else 0.0))

    if len(bars) < _MIN_BARS:
        raise RuntimeError(
            f"Not enough daily bars for {symbol}: "
            f"got {len(bars)}, need ≥{_MIN_BARS}"
        )
    return bars


def build_us_quote(symbol: str, bars: list[Bar]) -> dict[str, Any]:
    """Build a quote dict mimicking the A-share quote format.

    Returns keys: code, name, changepercent, amount.
    'amount' is approximated as volume × close (USD notional, no
    turnover data from Yahoo).
    """
    name = US_STOCK_NAMES.get(symbol, symbol)
    current = bars[-1]
    previous = bars[-2] if len(bars) >= 2 else current

    if previous.close != 0:
        change_pct = ((current.close - previous.close) / previous.close) * 100
    else:
        change_pct = 0.0
    amount = current.volume * current.close  # approximate USD notional

    return {
        "code": symbol,
        "name": name,
        "changepercent": round(change_pct, 2),
        "amount": amount,
    }


# ── Market indices ──────────────────────────────────────────────────────

_US_INDEX_SYMBOLS: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^DJI": "DJIA",
}


def fetch_us_index(index_symbol: str) -> dict[str, Any] | None:
    """Fetch a US market index (^GSPC, ^IXIC, ^DJI).

    Returns dict with keys: symbol, name, price, change_pct.
    Returns None on failure.
    """
    try:
        bars = fetch_us_daily_bars(index_symbol)
    except RuntimeError:
        return None
    if len(bars) < 2:
        return None
    current = bars[-1]
    previous = bars[-2]
    change_pct = (
        ((current.close - previous.close) / previous.close) * 100
        if previous.close != 0
        else 0.0
    )
    name = _US_INDEX_SYMBOLS.get(index_symbol, index_symbol)
    return {
        "symbol": index_symbol,
        "name": name,
        "price": round(current.close, 2),
        "change_pct": round(change_pct, 2),
    }


def fetch_us_indices() -> list[dict[str, Any]]:
    """Fetch all three major US indices (S&P 500, NASDAQ, DJIA)."""
    results: list[dict[str, Any]] = []
    for sym in _US_INDEX_SYMBOLS:
        idx = fetch_us_index(sym)
        if idx is not None:
            results.append(idx)
    return results


# ── Universe loading ────────────────────────────────────────────────────

def load_us_universe(path: str | None = None) -> list[str]:
    """Load US stock tickers from an optional watchlist file.

    Each line: SYMBOL or SYMBOL Name. Blank lines and #-comments skipped.
    Falls back to DEFAULT_US_UNIVERSE if path is None or unreadable.
    """
    if path is None:
        return list(DEFAULT_US_UNIVERSE)
    p = Path(path)
    if not p.is_file():
        return list(DEFAULT_US_UNIVERSE)
    tickers: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tickers.append(stripped.split()[0].upper())
    return tickers if tickers else list(DEFAULT_US_UNIVERSE)
