"""
Multi-model stock valuation engine.

Usage:
    python valuation_models.py <data_json_path>

Reads the JSON output from fetch_data.py, runs all applicable valuation models,
cross-validates them, and outputs a JSON file with:
    - models: individual model results
    - cross_validation: aggregated range and consensus
    - three_month_projection: 3-month price target

Models:
    1. PE Relative  — fair price = EPS × industry median PE
    2. PB Relative  — fair price = BPS × industry median PB
    3. PEG Model    — fair PE = earnings growth rate; price = EPS × fair PE
    4. DCF Simplified — 3yr FCF projection + terminal value − net debt
    5. Graham Formula — V = √(22.5 × EPS × BVPS)

A model is skipped if its required inputs are missing or unusable.
"""

import json
import math
import sys
import uuid
from datetime import datetime
from pathlib import Path


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sanitize_float(val, default=None):
    """Return val as float, or default if NaN/Inf/None."""
    if val is None:
        return default
    try:
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


class SafeEncoder(json.JSONEncoder):
    """JSON encoder that converts NaN/Infinity to null."""
    def encode(self, obj):
        return super().encode(self._sanitize(obj))

    def _sanitize(self, obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        elif isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._sanitize(v) for v in obj]
        return obj


# ── Valuation models ─────────────────────────────────────────────────────────

def _has_nan(*vals) -> bool:
    """Return True if any value is NaN."""
    return any(isinstance(v, float) and math.isnan(v) for v in vals)


def model_pe_relative(ratios: dict, industry: dict) -> dict | None:
    """PE-based relative valuation."""
    eps = ratios.get("eps", 0) or 0
    pe_ttm = ratios.get("pe_ttm")
    industry_pe = industry.get("industry_pe_median")

    if _has_nan(eps, industry_pe):
        return None
    if eps <= 0:
        return None
    if industry_pe is None or industry_pe <= 0:
        return None

    fair_price = eps * industry_pe
    return {
        "model": "PE相对估值",
        "fair_price": round(fair_price, 2),
        "inputs": {
            "eps": round(eps, 3),
            "industry_pe": round(industry_pe, 2),
            "current_pe": round(pe_ttm, 2) if pe_ttm else None,
        },
        "logic": f"EPS {eps:.2f} × 行业PE {industry_pe:.1f} = {fair_price:.2f}",
    }


def model_pb_relative(ratios: dict, industry: dict) -> dict | None:
    """PB-based relative valuation."""
    bvps = ratios.get("bvps", 0) or 0
    pb = ratios.get("pb")
    industry_pb = industry.get("industry_pb_median")

    if _has_nan(bvps, industry_pb):
        return None
    if bvps <= 0:
        return None
    if industry_pb is None or industry_pb <= 0:
        return None

    fair_price = bvps * industry_pb
    return {
        "model": "PB相对估值",
        "fair_price": round(fair_price, 2),
        "inputs": {
            "bvps": round(bvps, 2),
            "industry_pb": round(industry_pb, 2),
            "current_pb": round(pb, 2) if pb else None,
        },
        "logic": f"BVPS {bvps:.2f} × 行业PB {industry_pb:.1f} = {fair_price:.2f}",
    }


def model_peg(ratios: dict, growth: dict) -> dict | None:
    """PEG-based valuation. Fair PEG = 1."""
    eps = ratios.get("eps", 0) or 0
    pe_ttm = ratios.get("pe_ttm")
    earnings_growth = growth.get("earnings_growth_1yr_pct")

    if _has_nan(eps, earnings_growth, pe_ttm):
        return None
    if eps <= 0:
        return None
    if earnings_growth is None or earnings_growth <= 0:
        return None
    if pe_ttm is None or pe_ttm <= 0:
        return None

    # Cap growth at 30% for fair PE (unsustainable beyond that)
    fair_pe = min(earnings_growth, 30.0)
    fair_price = eps * fair_pe
    current_peg = pe_ttm / earnings_growth

    return {
        "model": "PEG估值",
        "fair_price": round(fair_price, 2),
        "inputs": {
            "eps": round(eps, 3),
            "earnings_growth_pct": round(earnings_growth, 2),
            "current_pe": round(pe_ttm, 2),
            "current_peg": round(current_peg, 2),
        },
        "logic": f"Fair PE = min(增长率,30) = {fair_pe:.1f} → {eps:.2f} × {fair_pe:.1f}",
        "note": f"当前PEG {current_peg:.2f}（{'高估' if current_peg > 1.5 else '合理' if current_peg > 0.8 else '低估'}）",
    }


def model_dcf(financials: dict, growth: dict, warnings_out: list | None = None) -> dict | None:
    """
    Simplified 3-year DCF model with net debt adjustment.

    FCF → project 3 years → terminal value → discount → enterprise value
    → subtract net debt → equity value → per-share price.
    """
    if warnings_out is None:
        warnings_out = []

    fcf = financials.get("fcf", 0) or 0
    if fcf <= 0:
        return None

    growth_rate = growth.get("revenue_growth_1yr_pct")
    if growth_rate is None:
        warnings_out.append("DCF: no growth rate available, skipping")
        return None
    if growth_rate == 0:
        warnings_out.append("DCF: growth rate is 0%, skipping (cannot project zero growth)")
        return None

    # Clamp growth: negative → near-zero with warning
    if growth_rate < 0:
        warnings_out.append(f"DCF: negative growth ({growth_rate:.1f}%) clamped to 1%")
        growth_rate = 1.0
    growth_rate = min(growth_rate, 30.0)

    g = growth_rate / 100.0
    wacc = 0.09
    terminal_g = 0.03

    fcf_0 = fcf
    fcf_1 = fcf_0 * (1 + g)
    fcf_2 = fcf_1 * (1 + g * 0.8)
    fcf_3 = fcf_2 * (1 + g * 0.6)

    terminal_value = fcf_3 * (1 + terminal_g) / (wacc - terminal_g)

    pv_fcf_1 = fcf_1 / (1 + wacc)
    pv_fcf_2 = fcf_2 / (1 + wacc) ** 2
    pv_fcf_3 = fcf_3 / (1 + wacc) ** 3
    pv_terminal = terminal_value / (1 + wacc) ** 3

    enterprise_value = pv_fcf_1 + pv_fcf_2 + pv_fcf_3 + pv_terminal

    # Net debt adjustment
    net_debt = financials.get("net_debt", 0) or 0
    equity_value = enterprise_value - net_debt

    if equity_value <= 0:
        warnings_out.append(
            f"DCF: equity value {equity_value:.1f}亿 ≤ 0 after net debt adjustment "
            f"(EV={enterprise_value:.1f}亿, net_debt={net_debt:.1f}亿). Skipping."
        )
        return None

    return {
        "model": "DCF简化",
        "fair_price": None,  # filled by caller with share count
        "_enterprise_value": round(enterprise_value, 2),
        "_equity_value": round(equity_value, 2),
        "_net_debt": round(net_debt, 2),
        "_fcf_projections": [round(fcf_1, 2), round(fcf_2, 2), round(fcf_3, 2)],
        "inputs": {
            "fcf_current_yi": round(fcf_0, 2),
            "growth_rate_pct": round(growth_rate, 2),
            "wacc_pct": round(wacc * 100, 2),
            "terminal_growth_pct": round(terminal_g * 100, 2),
            "net_debt_yi": round(net_debt, 2),
        },
        "logic": (
            f"FCF {fcf_0:.1f}亿, g={g*100:.1f}%, WACC={wacc*100:.0f}% "
            f"→ EV {enterprise_value:.0f}亿 − 净债务 {net_debt:.1f}亿 "
            f"= 权益 {equity_value:.0f}亿"
        ),
        "note": f"简化DCF，含净债务调整，WACC={wacc*100:.0f}%",
    }


def model_graham(ratios: dict) -> dict | None:
    """Graham number: V = √(22.5 × EPS × BVPS)."""
    eps = ratios.get("eps", 0) or 0
    bvps = ratios.get("bvps", 0) or 0

    if _has_nan(eps, bvps):
        return None
    if eps <= 0 and bvps <= 0:
        return None

    if eps <= 0:
        return None  # Graham requires positive EPS; skip rather than downgrade

    if bvps <= 0:
        # Fallback: use only EPS with Graham P/E cap of 15
        fair_price = eps * 15
        return {
            "model": "格雷厄姆（仅EPS）",
            "fair_price": round(fair_price, 2),
            "inputs": {"eps": round(eps, 3), "bvps": None},
            "logic": f"BVPS不可用，P/E=15上限: {eps:.2f} × 15",
            "note": "BVPS缺失或为负，仅使用EPS",
        }

    graham_number = math.sqrt(22.5 * eps * bvps)
    return {
        "model": "格雷厄姆公式",
        "fair_price": round(graham_number, 2),
        "inputs": {"eps": round(eps, 3), "bvps": round(bvps, 2)},
        "logic": f"√(22.5 × {eps:.2f} × {bvps:.2f}) = {graham_number:.2f}",
        "note": "基于本杰明·格雷厄姆价值投资公式",
    }


# ── Cross-validation ─────────────────────────────────────────────────────────

def cross_validate(results: list[dict], current_price: float) -> dict:
    """
    Cross-validate model results.

    Uses median-centered MAD (Median Absolute Deviation) for outlier detection
    and builds a valuation range centered on the median of core models.
    The range is guaranteed to contain the median.

    Consensus thresholds (unified with docs):
        strong:   core dispersion < 20%
        moderate: core dispersion < 50%
        weak:     core dispersion ≥ 50%
    """
    valid = [r for r in results if r and r.get("fair_price") and r["fair_price"] > 0]
    if not valid:
        return {
            "median_price": None,
            "range_low": None,
            "range_high": None,
            "consensus": "insufficient_data",
            "outliers": [],
            "valid_models": 0,
            "error": "No valid model results",
        }

    prices = [r["fair_price"] for r in valid]
    prices_sorted = sorted(prices)
    n = len(prices_sorted)

    # Median
    if n % 2 == 1:
        median = prices_sorted[n // 2]
    else:
        median = (prices_sorted[n // 2 - 1] + prices_sorted[n // 2]) / 2

    # MAD (Median Absolute Deviation)
    deviations = [abs(p - median) for p in prices]
    deviations_sorted = sorted(deviations)
    if len(deviations_sorted) % 2 == 1:
        mad = deviations_sorted[len(deviations_sorted) // 2]
    else:
        mid = len(deviations_sorted) // 2
        mad = (deviations_sorted[mid - 1] + deviations_sorted[mid]) / 2

    # Outlier: > 2 × MAD from median, OR > 30% from median (whichever is smaller)
    threshold = min(2 * mad, 0.30 * median) if mad > 0 else 0.30 * median
    if threshold <= 0:
        threshold = 0.30 * median

    outliers = []
    core_prices = []
    for r in valid:
        deviation = abs(r["fair_price"] - median)
        if deviation > threshold and threshold > 0:
            outliers.append({**r, "_deviation_pct": round(deviation / median * 100, 1)})
        else:
            core_prices.append(r["fair_price"])

    if not core_prices:
        core_prices = prices
        outliers = []

    # Build range centered on MEDIAN, using core model spread
    core_n = len(core_prices)
    if core_n >= 2:
        # Use median ± 1.5 × MAD of core models as the range
        core_median = sorted(core_prices)[core_n // 2] if core_n % 2 == 1 else \
            (sorted(core_prices)[core_n // 2 - 1] + sorted(core_prices)[core_n // 2]) / 2
        core_deviations = [abs(p - core_median) for p in core_prices]
        core_deviations_sorted = sorted(core_deviations)
        if len(core_deviations_sorted) % 2 == 1:
            core_mad = core_deviations_sorted[len(core_deviations_sorted) // 2]
        else:
            mid = len(core_deviations_sorted) // 2
            core_mad = (core_deviations_sorted[mid - 1] + core_deviations_sorted[mid]) / 2

        half_range = max(1.5 * core_mad, core_median * 0.03)  # min 3% spread
        lo = core_median - half_range
        hi = core_median + half_range

        # Enforce max 25% spread of midpoint
        midpoint = (lo + hi) / 2
        if (hi - lo) / midpoint > 0.25:
            lo = midpoint * 0.875
            hi = midpoint * 1.125
            spread_adjusted = "capped_at_25pct"
        elif half_range < core_median * 0.03:
            spread_adjusted = "min_3pct"
        else:
            spread_adjusted = False
    else:
        lo = core_prices[0] * 0.90
        hi = core_prices[0] * 1.10
        midpoint = core_prices[0]
        spread_adjusted = "single_model_fallback"

    # Consensus
    core_dispersion = None
    if core_n >= 2:
        core_max = max(core_prices)
        core_min = min(core_prices)
        core_dispersion = (core_max - core_min) / ((core_max + core_min) / 2)
        if core_dispersion < 0.20:
            consensus = "strong"
        elif core_dispersion < 0.50:
            consensus = "moderate"
        else:
            consensus = "weak"
    elif core_n == 1:
        consensus = "weak"
    else:
        consensus = "weak"

    return {
        "median_price": round(median, 2),
        "range_low": round(lo, 2),
        "range_high": round(hi, 2),
        "range_midpoint": round((lo + hi) / 2, 2),
        "range_spread_pct": round((hi - lo) / ((lo + hi) / 2) * 100, 1),
        "spread_adjusted": spread_adjusted,
        "consensus": consensus,
        "dispersion_pct": round(core_dispersion * 100, 1) if core_dispersion else None,
        "outliers": outliers,
        "valid_models": n,
        "core_models": core_n,
        "all_models": len(results),
    }


# ── 3-month projection ───────────────────────────────────────────────────────

def project_3month(
    current_price: float,
    fair_range: tuple[float, float],
    sentiment_factor: float = 0.0,
    volatility_hint: float = 0.30,
    warnings_out: list | None = None,
) -> dict:
    """
    Project the 3-month price range from current price toward fair value.

    Args:
        current_price: current stock price (must be > 0)
        fair_range: (low, high) from cross-validation
        sentiment_factor: -5.0 to +5.0 from news sentiment
        volatility_hint: annualized volatility estimate (default 30%)
        warnings_out: optional list to append warnings to

    Raises:
        ValueError: if current_price <= 0
    """
    if warnings_out is None:
        warnings_out = []

    if current_price <= 0:
        raise ValueError(f"current_price must be > 0, got {current_price}")

    # Unpack and validate fair_range
    fair_low, fair_high = fair_range
    if fair_low > fair_high:
        warnings_out.append(
            f"project_3month: fair_range inverted ({fair_low}, {fair_high}), swapping"
        )
        fair_low, fair_high = fair_high, fair_low

    # Clamp sentiment_factor to [-5, +5]
    sentiment_factor = max(-5.0, min(5.0, sentiment_factor))

    # Partial convergence: 20% of gap closes in 3 months
    convergence = 0.20
    target_low = current_price + (fair_low - current_price) * convergence
    target_high = current_price + (fair_high - current_price) * convergence

    # Sentiment adjustment
    sentiment_adj = sentiment_factor / 100.0
    target_low *= (1 + sentiment_adj)
    target_high *= (1 + sentiment_adj)

    # Volatility buffer: quarterly vol × 0.5 sigma
    quarterly_vol = volatility_hint * math.sqrt(3.0 / 12.0)  # = vol * 0.5
    buffer = quarterly_vol * 0.5
    target_low *= (1 - buffer)
    target_high *= (1 + buffer)

    midpoint = (target_low + target_high) / 2
    potential_return = (midpoint / current_price - 1) * 100

    return {
        "target_low": round(target_low, 2),
        "target_high": round(target_high, 2),
        "midpoint": round(midpoint, 2),
        "potential_return_pct": round(potential_return, 2),
        "convergence_rate_pct": round(convergence * 100, 1),
        "sentiment_adjustment_pct": round(sentiment_adj * 100, 2),
        "quarterly_vol_buffer_pct": round(buffer * 100, 2),
        "volatility_scaling_note": "quarterly_vol = annual_vol × √(3/12), buffer = 0.5σ",
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python valuation_models.py <data_json_path> [sentiment_factor]",
              file=sys.stderr)
        sys.exit(1)

    data_path = Path(sys.argv[1])
    if not data_path.exists():
        print(f"Error: file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    sentiment_factor = _sanitize_float(sys.argv[2], 0.0) if len(sys.argv) > 2 else 0.0
    sentiment_factor = max(-5.0, min(5.0, sentiment_factor))

    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    basic = data.get("basic_info", {})
    financials = data.get("financials", {})
    ratios = data.get("ratios", {})
    industry = data.get("industry", {})
    growth = data.get("growth_rates", {})

    current_price = basic.get("current_price", 0) or 0
    market_cap = basic.get("market_cap", 0) or 0
    net_profit = financials.get("net_profit", 0) or 0
    eps = ratios.get("eps", 0) or 0

    all_warnings = []
    all_errors = []

    # Shares for per-share conversion
    if current_price > 0 and market_cap > 0:
        shares = market_cap / current_price
    elif eps > 0 and net_profit > 0:
        shares = (net_profit * 1e8) / eps
    else:
        shares = None

    # ── Run models ───────────────────────────────────────────────────────

    models = []

    pe = model_pe_relative(ratios, industry)
    models.append(pe if pe else {
        "model": "PE相对估值", "fair_price": None,
        "skipped": True, "reason": "EPS为负或行业PE不可用",
    })

    pb = model_pb_relative(ratios, industry)
    models.append(pb if pb else {
        "model": "PB相对估值", "fair_price": None,
        "skipped": True, "reason": "BVPS为负或行业PB不可用",
    })

    peg = model_peg(ratios, growth)
    models.append(peg if peg else {
        "model": "PEG估值", "fair_price": None,
        "skipped": True, "reason": "EPS为负或增长率为负/不可用",
    })

    dcf = model_dcf(financials, growth, all_warnings)
    if dcf and dcf.get("_equity_value"):
        ev = dcf["_equity_value"]
        if shares and shares > 0:
            dcf["fair_price"] = round(ev * 1e8 / shares, 2)
            dcf["logic"] += f" → {dcf['fair_price']:.2f}/股"
        else:
            all_warnings.append("DCF: cannot compute per-share price (no share count)")
            dcf["fair_price"] = None
        dcf.pop("_enterprise_value", None)
        dcf.pop("_equity_value", None)
        dcf.pop("_net_debt", None)
        dcf.pop("_fcf_projections", None)
        models.append(dcf)
    else:
        models.append({
            "model": "DCF简化", "fair_price": None,
            "skipped": True,
            "reason": dcf.get("_reason") if dcf and isinstance(dcf, dict) and "_reason" in dcf
                     else "自由现金流为负或净债务过高",
        })

    graham = model_graham(ratios)
    models.append(graham if graham else {
        "model": "格雷厄姆公式", "fair_price": None,
        "skipped": True, "reason": "EPS和BVPS均为负",
    })

    # ── Cross-validate ───────────────────────────────────────────────────

    cv = cross_validate(models, current_price)

    # ── 3-month projection ──────────────────────────────────────────────

    try:
        proj = None
        if cv.get("range_low") and cv.get("range_high") and current_price > 0:
            proj = project_3month(
                current_price,
                (cv["range_low"], cv["range_high"]),
                sentiment_factor=sentiment_factor,
                warnings_out=all_warnings,
            )
    except ValueError as e:
        all_errors.append(f"project_3month failed: {e}")
        proj = None

    # ── Assemble output ─────────────────────────────────────────────────

    output = {
        "meta": {
            "valuation_date": datetime.now().isoformat(),
            "source_data": str(data_path),
            "stock_name": basic.get("name", "Unknown"),
            "stock_code": basic.get("code", "Unknown"),
            "market": basic.get("market", "Unknown"),
            "current_price": current_price,
            "sentiment_factor": sentiment_factor,
        },
        "models": models,
        "cross_validation": cv,
        "three_month_projection": proj,
        "warnings": all_warnings,
        "errors": all_errors,
    }

    # Write output (with unique filename)
    output_dir = Path(__file__).parent.parent / ".cache"
    output_dir.mkdir(exist_ok=True)
    code = basic.get("code", "unknown")
    unique_id = uuid.uuid4().hex[:8]
    output_file = output_dir / f"valuation_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{unique_id}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, cls=SafeEncoder)

    if all_errors:
        for err in all_errors:
            print(f"[valuation] ERROR: {err}", file=sys.stderr)
    if all_warnings:
        for warn in all_warnings:
            print(f"[valuation] WARNING: {warn}", file=sys.stderr)

    print(str(output_file.resolve()))


if __name__ == "__main__":
    main()
