"""
Stock financial data fetcher for A-share (A股) and US stocks.

Usage:
    python fetch_data.py <stock_code> <market>

Data sources:
    A-share real-time: Sina Finance (hq.sinajs.cn) — no API key needed
    A-share financials: akshare (eastmoney report APIs)
    US stocks: yfinance (Yahoo Finance)

Output: JSON file path printed to stdout.
"""

import json
import os
import re
import sys
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

# ── Proxy bypass (Windows system proxy blocks eastmoney) ─────────────────────

# Force urllib to report no proxies so downstream libs don't use the
# system-registry proxy that can't reach Chinese financial data hosts.
try:
    import urllib.request
    urllib.request.getproxies = lambda: {}
except Exception:
    pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        v = float(val)
        if v != v:
            return default
        return v
    except (ValueError, TypeError):
        return default


def _sanitize_code(code: str) -> str:
    code = code.strip().upper()
    if not re.match(r'^[A-Za-z0-9.]+$', code):
        raise ValueError(f"Invalid stock code: {code!r}. Only [A-Za-z0-9.] allowed.")
    return code


def _yi(val):
    """Convert 元 → 亿 (÷1e8). Returns float; None/NaN → 0."""
    v = _safe_float(val, 0.0)
    return v / 1e8 if v else 0.0


def _check_dependency(pkg_name: str) -> bool:
    try:
        __import__(pkg_name)
        return True
    except ImportError:
        print(
            f"[fetch_data] ERROR: '{pkg_name}' is not installed. "
            f"Run: pip install {pkg_name}",
            file=sys.stderr,
        )
        return False


# ── Fetch result ─────────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Tencent real-time quote (A-share primary) ────────────────────────────────

def _tencent_quote(code: str) -> dict | None:
    """
    Fetch real-time quote from Tencent Finance (qt.gtimg.cn).

    Returns dict with: name, price, pe_ttm, pb, market_cap (in 亿),
    circulating_market_cap (in 亿), high, low, prev_close, change_pct.
    Returns None on failure.
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"

    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        s.proxies = {"http": "", "https": ""}
        r = s.get(f"https://qt.gtimg.cn/q={symbol}", timeout=10)
        if r.status_code != 200:
            return None

        text = r.text
        if "=" not in text:
            return None
        raw = text.split('"')[1] if '"' in text else ""
        if not raw:
            return None
        fields = raw.split("~")
        if len(fields) < 47:
            return None

        # Tencent field map (0-indexed, ~88 fields total):
        # [1]=name, [3]=price, [4]=prev_close, [31]=change, [32]=change_pct
        # [33]=high, [34]=low, [39]=PE, [44]=circulating_mcap(亿), [45]=total_mcap(亿), [46]=PB
        return {
            "name": fields[1],
            "price": _safe_float(fields[3], 0.0),
            "prev_close": _safe_float(fields[4]),
            "change_pct": _safe_float(fields[32]),
            "high": _safe_float(fields[33]),
            "low": _safe_float(fields[34]),
            "pe_ttm": _safe_float(fields[39]),
            "pb": _safe_float(fields[46]),
            "market_cap_yi": _safe_float(fields[45], 0.0),          # 总市值(亿)
            "circ_market_cap_yi": _safe_float(fields[44]),          # 流通市值(亿)
        }
    except Exception:
        return None


# ── A-share data fetching ────────────────────────────────────────────────────

def fetch_a_share(code: str) -> FetchResult:
    result = FetchResult()

    if not _check_dependency("akshare"):
        result.errors.append("akshare not installed — cannot fetch A-share financials")
        return result

    import akshare as ak

    raw_code = _sanitize_code(code)
    symbol = f"SH{raw_code}" if raw_code.startswith(("6", "9")) else f"SZ{raw_code}"
    print(f"[fetch_data] Fetching A-share data for {raw_code} (symbol={symbol})...",
          file=sys.stderr)

    # ── Real-time quote (Tencent) ───────────────────────────────────────
    quote = _tencent_quote(raw_code)
    stock_name = raw_code
    current_price = 0.0
    market_cap_yi = 0.0
    pe_from_quote = None
    pb_from_quote = None

    if quote:
        stock_name = quote["name"]
        current_price = quote["price"]
        market_cap_yi = quote["market_cap_yi"]  # already in 亿
        pe_from_quote = quote["pe_ttm"]
        pb_from_quote = quote["pb"]
        print(f"[fetch_data] Tencent quote: {stock_name} price={current_price} "
              f"mcap={market_cap_yi}亿 PE={pe_from_quote} PB={pb_from_quote}",
              file=sys.stderr)
    else:
        result.warnings.append("Tencent quote failed")

    market_cap = market_cap_yi * 1e8  # 亿 → 元
    if current_price <= 0:
        result.errors.append("Cannot determine current price")

    # ── Financial statements (akshare — English column names) ───────────

    def _fetch_df(fn, **kw):
        try:
            return fn(**kw)
        except Exception as e:
            result.errors.append(f"{fn.__name__} failed: {e}")
            return None

    def _col_val(df, col_name: str):
        """Extract the LATEST row's value for an English column name."""
        if df is None or df.empty:
            return None
        if col_name not in df.columns:
            for c in df.columns:
                if c.upper() == col_name.upper():
                    col_name = c
                    break
            else:
                result.warnings.append(f"Column '{col_name}' not found")
                return None
        vals = df[col_name].dropna()
        if len(vals) == 0:
            return None
        return _safe_float(vals.iloc[0])

    def _col_val_annual(df, col_name: str):
        """
        Extract value from the latest ANNUAL report row (年报).
        Falls back to latest row if no annual report found.
        """
        if df is None or df.empty:
            return None
        if col_name not in df.columns:
            for c in df.columns:
                if c.upper() == col_name.upper():
                    col_name = c
                    break
            else:
                return None
        # Find row with '年报' in REPORT_DATE_NAME
        if "REPORT_DATE_NAME" in df.columns:
            for i in range(len(df)):
                if "年报" in str(df.iloc[i].get("REPORT_DATE_NAME", "")):
                    return _safe_float(df.iloc[i][col_name])
        # Fallback to latest
        vals = df[col_name].dropna()
        return _safe_float(vals.iloc[0]) if len(vals) > 0 else None

    def _growth(df, col: str):
        """YoY growth comparing same-period-type reports (e.g. Q1 vs Q1, FY vs FY)."""
        if df is None or df.empty or col not in df.columns:
            return None
        if "REPORT_DATE_NAME" not in df.columns:
            return None

        # Find pairs of same-type periods
        periods = df["REPORT_DATE_NAME"].tolist()
        vals = df[col].tolist()

        for i, period in enumerate(periods):
            # Look for same period type exactly 4 rows ahead (quarterly) or 1 (annual)
            for j in range(i + 1, min(i + 6, len(periods))):
                ptype = "".join(c for c in str(period) if c.isalpha()) if period else ""
                ptype_j = "".join(c for c in str(periods[j]) if c.isalpha()) if periods[j] else ""
                if ptype and ptype == ptype_j:
                    cur = _safe_float(vals[i])
                    prev = _safe_float(vals[j])
                    if cur and prev and prev != 0:
                        return (cur / prev - 1) * 100
            return None
        return None

    income_df = _fetch_df(ak.stock_profit_sheet_by_report_em, symbol=symbol)
    balance_df = _fetch_df(ak.stock_balance_sheet_by_report_em, symbol=symbol)
    cashflow_df = _fetch_df(ak.stock_cash_flow_sheet_by_report_em, symbol=symbol)

    # Extract key metrics (all in 元 → convert to 亿)
    # Income statement: prefer annual report (年报) for flow variables
    total_revenue = _yi(_col_val_annual(income_df, "TOTAL_OPERATE_INCOME"))
    net_profit = _yi(_col_val_annual(income_df, "NETPROFIT"))
    parent_np = _yi(_col_val_annual(income_df, "PARENT_NETPROFIT"))
    # Balance sheet: latest period (point-in-time)
    total_assets = _yi(_col_val(balance_df, "TOTAL_ASSETS"))
    total_equity = _yi(_col_val(balance_df, "TOTAL_PARENT_EQUITY"))
    if total_equity == 0:
        total_equity = _yi(_col_val(balance_df, "TOTAL_EQUITY"))
    # Cash flow: prefer annual
    operating_cf = _yi(_col_val_annual(cashflow_df, "NETCASH_OPERATE"))
    capex_val_raw = _col_val_annual(cashflow_df, "CONSTRUCT_LONG_ASSET") or 0
    capex = -abs(_yi(capex_val_raw))  # force negative: cash OUTFLOW
    fcf = operating_cf + capex

    # Net debt: A-share balance sheets rarely have explicit total debt
    # Use 0 with a warning — DCF will skip net debt adjustment for A-shares
    net_debt = 0.0
    total_debt = _yi(None)  # unavailable

    # Industry PE/PB — from cninfo API (different host, not blocked by proxy)
    industry_pe = None
    industry_pb = None
    try:
        from datetime import date
        today_str = date.today().strftime("%Y%m%d")
        ind_df = ak.stock_industry_pe_ratio_cninfo(
            symbol="证监会行业分类", date=today_str
        )
        if ind_df is not None and not ind_df.empty and len(ind_df) > 0:
            # Columns: 行业名称, 静态市盈率-加权平均, 静态市盈率-中位数, 静态市盈率-简单平均
            # Match stock to industry via company name keywords
            pe_col = None
            for c in ind_df.columns:
                if "中位数" in str(c) and "市盈" in str(c):
                    pe_col = c
                    break
            if pe_col is None:
                for c in ind_df.columns:
                    if "市盈" in str(c):
                        pe_col = c
                        break

            # Try to match stock to its industry from name keywords
            name_kw = stock_name[:3] if len(stock_name) >= 3 else stock_name
            for _, row in ind_df.iterrows():
                industry_name = str(row.get("行业名称", ""))
                if name_kw in industry_name or (
                    "电子" in industry_name and any(
                        kw in stock_name for kw in ["科技", "电子", "微", "芯", "半导", "光电"]
                    )
                ):
                    industry_pe = _safe_float(row.get(pe_col)) if pe_col else None
                    result.warnings.append(
                        f"Matched industry '{industry_name}' PE={industry_pe}"
                    )
                    break

            # Fallback: if no match, use the 电子/制造 broad category
            if industry_pe is None:
                for _, row in ind_df.iterrows():
                    industry_name = str(row.get("行业名称", ""))
                    if "计算机" in industry_name or "电子" in industry_name:
                        industry_pe = _safe_float(row.get(pe_col)) if pe_col else None
                        result.warnings.append(
                            f"Fallback industry '{industry_name}' PE={industry_pe}"
                        )
                        break
    except Exception as e:
        result.warnings.append(f"Industry PE/PB unavailable: {str(e)[:120]}")

    # ── Compute ratios ─────────────────────────────────────────────────
    shares = market_cap / current_price if current_price > 0 and market_cap > 0 else 0
    shares_yi = shares / 1e8 if shares > 0 else 0  # shares in 亿股

    # EPS/BVPS: net_profit and equity are in 亿CNY, shares_yi is in 亿股 → result is CNY/share
    eps_val = parent_np / shares_yi if shares_yi > 0 else 0
    bvps_val = total_equity / shares_yi if shares_yi > 0 else 0

    pe_ttm = current_price / eps_val if eps_val > 0 else None
    pb_val = current_price / bvps_val if bvps_val > 0 else None
    ps_val = (market_cap / 1e8) / total_revenue if total_revenue > 0 and market_cap > 0 else None
    roe_val = (parent_np / total_equity * 100) if total_equity > 0 else None

    # Prefer Tencent quote's PE/PB (from market data) over computed values
    if pe_from_quote and pe_from_quote > 0:
        pe_ttm = pe_from_quote
    if pb_from_quote and pb_from_quote > 0:
        pb_val = pb_from_quote

    # ── Growth ──────────────────────────────────────────────────────────
    rev_g = _growth(income_df, "TOTAL_OPERATE_INCOME")
    earn_g = _growth(income_df, "NETPROFIT")

    # ── Historical excerpts ─────────────────────────────────────────────
    def _history(df, cols, n=3):
        if df is None or df.empty:
            return []
        rows = []
        available = [c for c in cols if c in df.columns]
        for i in range(min(n, len(df))):
            row = {}
            for c in available:
                v = _safe_float(df.iloc[i][c])
                row[c] = round(v / 1e8, 2) if v is not None and abs(v) > 1e4 else v
            period = str(df.iloc[i].get("REPORT_DATE_NAME", i))
            rows.append({"period": period, "values": row})
        return rows

    income_hist = _history(income_df, ["TOTAL_OPERATE_INCOME", "NETPROFIT", "PARENT_NETPROFIT"])
    balance_hist = _history(balance_df, ["TOTAL_ASSETS", "TOTAL_PARENT_EQUITY"])
    cf_hist = _history(cashflow_df, ["NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET"])

    # ── Assemble result ────────────────────────────────────────────────
    missing_count = sum(
        1 for v in [total_revenue, net_profit, total_assets, total_equity, operating_cf]
        if v == 0
    )

    result.data = {
        "basic_info": {
            "name": str(stock_name),
            "code": raw_code,
            "market": "A-share",
            "current_price": current_price,
            "market_cap": market_cap,
            "market_cap_display": f"{market_cap / 1e8:.0f}亿" if market_cap > 1e6 else "N/A",
        },
        "financials": {
            "total_revenue": round(total_revenue, 2),
            "net_profit": round(net_profit, 2),
            "total_assets": round(total_assets, 2),
            "total_equity": round(total_equity, 2),
            "total_debt": round(total_debt, 2),
            "cash_and_equivalents": 0,
            "net_debt": round(net_debt, 2),
            "operating_cf": round(operating_cf, 2),
            "capex": round(capex, 2),
            "fcf": round(fcf, 2),
            "unit": "亿CNY",
        },
        "ratios": {
            "eps": round(eps_val, 3),
            "bvps": round(bvps_val, 2),
            "pe_ttm": round(pe_ttm, 2) if pe_ttm else None,
            "pb": round(pb_val, 2) if pb_val else None,
            "ps": round(ps_val, 2) if ps_val else None,
            "roe_pct": round(roe_val, 2) if roe_val else None,
            "dividend_yield_pct": None,
        },
        "industry": {
            "industry_pe_median": round(industry_pe, 2) if industry_pe else None,
            "industry_pb_median": round(industry_pb, 2) if industry_pb else None,
        },
        "growth_rates": {
            "revenue_growth_1yr_pct": round(rev_g, 2) if rev_g is not None else None,
            "earnings_growth_1yr_pct": round(earn_g, 2) if earn_g is not None else None,
        },
        "history": {
            "income": income_hist,
            "balance": balance_hist,
            "cashflow": cf_hist,
        },
        "errors": result.errors,
        "warnings": result.warnings,
        "data_quality": {
            "fields_missing": missing_count,
            "total_fields": 5,
        },
        "meta": {
            "fetch_date": datetime.now().isoformat(),
            "source": "sina+akshare",
        },
    }

    return result


# ── US stock data fetching (yfinance) ────────────────────────────────────────

def fetch_us(ticker: str) -> FetchResult:
    result = FetchResult()

    if not _check_dependency("yfinance"):
        result.errors.append("yfinance not installed — cannot fetch US data")
        return result

    import yfinance as yf

    ticker = _sanitize_code(ticker)
    print(f"[fetch_data] Fetching US data for {ticker}...", file=sys.stderr)

    YI = 1e8

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
    except Exception as e:
        result.errors.append(f"yfinance.Ticker({ticker}) failed: {e}")
        return result

    stock_name = info.get("longName") or info.get("shortName") or ticker
    current_price = _safe_float(info.get("currentPrice") or info.get("previousClose"), 0.0)
    market_cap = _safe_float(info.get("marketCap"), 0.0)

    def _df_to_dict(df):
        if df is None or df.empty:
            return []
        result_list = []
        for col in df.columns[:3]:
            period_data = {}
            for idx in df.index:
                try:
                    v = _safe_float(df.loc[idx, col])
                    period_data[str(idx)] = round(v / YI, 2) if v is not None and abs(v) > 1e6 else v
                except Exception:
                    period_data[str(idx)] = str(df.loc[idx, col]) if df.loc[idx, col] is not None else None
            date_str = str(col).split("T")[0] if hasattr(col, "strftime") else str(col)[:10]
            result_list.append({"period": date_str, "values": period_data})
        return result_list

    try:
        income_df = stock.financials
    except Exception as e:
        result.errors.append(f"stock.financials failed: {e}")
        income_df = None
    try:
        balance_df = stock.balance_sheet
    except Exception as e:
        result.errors.append(f"stock.balance_sheet failed: {e}")
        balance_df = None
    try:
        cashflow_df = stock.cashflow
    except Exception as e:
        result.errors.append(f"stock.cashflow failed: {e}")
        cashflow_df = None

    income_history = _df_to_dict(income_df)
    balance_history = _df_to_dict(balance_df)
    cashflow_history = _df_to_dict(cashflow_df)

    total_revenue = _safe_float(info.get("totalRevenue"), 0.0) / YI
    net_income = _safe_float(info.get("netIncomeToCommon"), 0.0) / YI
    total_assets = _safe_float(info.get("totalAssets"), 0.0) / YI
    total_equity = _safe_float(info.get("totalStockholderEquity"), 0.0) / YI
    operating_cf = _safe_float(info.get("operatingCashflow"), 0.0) / YI
    capex = _safe_float(info.get("capitalExpenditures"), 0.0) / YI
    fcf = _safe_float(info.get("freeCashflow"), 0.0) / YI
    if fcf == 0:
        fcf = operating_cf + capex

    total_debt = _safe_float(info.get("totalDebt"), 0.0) / YI
    cash_eq = _safe_float(info.get("totalCash") or info.get("cash"), 0.0) / YI
    net_debt = total_debt - cash_eq

    eps = _safe_float(info.get("trailingEps"), 0.0)
    bvps = _safe_float(info.get("bookValue"), 0.0)
    pe_ttm = _safe_float(info.get("trailingPE") or (current_price / eps if eps > 0 else None))
    pb = _safe_float(info.get("priceToBook") or (current_price / bvps if bvps > 0 else None))
    ps = _safe_float(info.get("priceToSalesTrailing12Months"))
    roe = _safe_float(info.get("returnOnEquity"), 0.0)
    if roe and abs(roe) < 1:
        roe *= 100
    div_yield = _safe_float(info.get("dividendYield"), 0.0)
    if div_yield and abs(div_yield) < 1:
        div_yield *= 100

    revenue_growth = _safe_float(info.get("revenueGrowth"), 0.0)
    if revenue_growth and abs(revenue_growth) < 1:
        revenue_growth *= 100
    earnings_growth = _safe_float(info.get("earningsGrowth"), 0.0)
    if earnings_growth and abs(earnings_growth) < 1:
        earnings_growth *= 100

    sector = info.get("sector", "")
    industry_pe = _safe_float(info.get("industryPe"))
    industry_pb = _safe_float(info.get("industryPb"))

    result.data = {
        "basic_info": {
            "name": str(stock_name), "code": ticker, "market": "US",
            "sector": sector, "current_price": current_price, "market_cap": market_cap,
            "market_cap_display": f"${market_cap / YI:.0f}亿" if market_cap > 1e6 else str(market_cap),
        },
        "financials": {
            "total_revenue": round(total_revenue, 2),
            "net_profit": round(net_income, 2),
            "total_assets": round(total_assets, 2),
            "total_equity": round(total_equity, 2),
            "total_debt": round(total_debt, 2),
            "cash_and_equivalents": round(cash_eq, 2),
            "net_debt": round(net_debt, 2),
            "operating_cf": round(operating_cf, 2),
            "capex": round(capex, 2),
            "fcf": round(fcf, 2),
            "unit": "亿USD",
        },
        "ratios": {
            "eps": round(eps, 3) if eps is not None else 0,
            "bvps": round(bvps, 2) if bvps is not None else 0,
            "pe_ttm": round(pe_ttm, 2) if pe_ttm else None,
            "pb": round(pb, 2) if pb else None,
            "ps": round(ps, 2) if ps else None,
            "roe_pct": round(roe, 2) if roe else None,
            "dividend_yield_pct": round(div_yield, 2) if div_yield else None,
        },
        "industry": {
            "industry_pe_median": round(industry_pe, 2) if industry_pe else None,
            "industry_pb_median": round(industry_pb, 2) if industry_pb else None,
        },
        "growth_rates": {
            "revenue_growth_1yr_pct": round(revenue_growth, 2) if revenue_growth else None,
            "earnings_growth_1yr_pct": round(earnings_growth, 2) if earnings_growth else None,
        },
        "history": {"income": income_history, "balance": balance_history, "cashflow": cashflow_history},
        "errors": result.errors,
        "warnings": result.warnings,
        "data_quality": {
            "fields_missing": sum(1 for v in [
                info.get("totalRevenue"), info.get("netIncomeToCommon"),
                info.get("totalStockholderEquity"),
            ] if v is None),
            "total_fields": 3,
        },
        "meta": {"fetch_date": datetime.now().isoformat(), "source": "yfinance"},
    }

    return result


# ── Market detection ─────────────────────────────────────────────────────────

def detect_market(code: str) -> str:
    code = code.strip().upper()
    if code.isdigit() and len(code) == 6:
        return "a-share"
    elif code.isalpha() and 1 <= len(code) <= 5:
        return "us"
    return "a-share" if code.isdigit() else "us"


# ── NaN-safe encoder ─────────────────────────────────────────────────────────

class SafeEncoder(json.JSONEncoder):
    def encode(self, obj):
        return super().encode(self._sanitize(obj))

    def _sanitize(self, obj):
        import math
        if isinstance(obj, float):
            return None if math.isnan(obj) or math.isinf(obj) else obj
        elif isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._sanitize(v) for v in obj]
        return obj


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_data.py <stock_code> [market: a-share|us]", file=sys.stderr)
        sys.exit(1)

    code = _sanitize_code(sys.argv[1])
    market = sys.argv[2] if len(sys.argv) > 2 else detect_market(code)

    if market.lower() in ("a-share", "a_share", "ashare", "cn"):
        result = fetch_a_share(code)
    elif market.lower() in ("us", "usa", "nyse", "nasdaq"):
        result = fetch_us(code)
    else:
        print(f"Unknown market: {market}. Use 'a-share' or 'us'.", file=sys.stderr)
        sys.exit(1)

    if not result.data:
        result.data = {"error": "No data fetched", "errors": result.errors}
        result.errors.append("Completely empty result")

    output_dir = Path(__file__).parent.parent / ".cache"
    output_dir.mkdir(exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    output_file = output_dir / f"data_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uid}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result.data, f, ensure_ascii=False, indent=2, cls=SafeEncoder)

    for err in result.errors:
        print(f"[fetch_data] ERROR: {err}", file=sys.stderr)
    for warn in result.warnings:
        print(f"[fetch_data] WARNING: {warn}", file=sys.stderr)

    print(str(output_file.resolve()))


if __name__ == "__main__":
    main()
