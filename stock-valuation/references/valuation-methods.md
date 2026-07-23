# Valuation Methods Reference

Detailed methodology for each valuation model used by this skill.
Read this when explaining a model's result or debugging unexpected output.

---

## Table of Contents
1. [PE Relative Valuation](#1-pe-relative-valuation)
2. [PB Relative Valuation](#2-pb-relative-valuation)
3. [PEG Model](#3-peg-model)
4. [Simplified DCF](#4-simplified-dcf)
5. [Graham Formula](#5-graham-formula)
6. [Cross-Validation](#6-cross-validation)
7. [3-Month Projection](#7-3-month-projection)

---

## 1. PE Relative Valuation

**Formula**: Fair Price = EPS × Industry Median PE

**When it applies**:
- Company is profitable (positive EPS)
- Industry median PE is available and meaningful
- Best for: mature companies with stable earnings in well-defined industries

**When it DOESN'T apply**:
- Negative EPS (loss-making company) — skip
- Industry PE unavailable — skip
- Cyclical industries at peak earnings — flag as potentially misleading

**Industry PE source**:
- A-share: akshare `stock_pe_pb_industry_em()` — 东方财富 industry classification
- US: yfinance `info["industryPe"]` when available; otherwise WebSearch for sector average

**Edge cases**:
- Company PE << Industry PE: might be undervalued OR might have structural problems
- Company PE >> Industry PE: might have moat/competitive advantage — apply 10-15% premium
- Industry PE > 100: likely a bubble or high-growth sector, flag and reduce weight

---

## 2. PB Relative Valuation

**Formula**: Fair Price = BVPS × Industry Median PB

**When it applies**:
- Company has positive book value
- Industry median PB is available
- Best for: financials (banks, insurance), asset-heavy industries, REITs

**When it DOESN'T apply**:
- Negative book value — skip
- Tech/asset-light companies — PB less meaningful, reduce weight
- Goodwill-heavy balance sheets — book value may be inflated

**Limitations**:
- Book value ≠ liquidation value
- Intangible assets (brand, patents) not reflected
- Accounting differences between markets affect comparability

---

## 3. PEG Model

**Formula**: Fair PE = Earnings Growth Rate; Fair Price = EPS × Fair PE

**Core assumption**: PEG ratio of 1.0 represents fair value.

**When it applies**:
- Positive earnings AND positive earnings growth
- Growth rate is sustainable (not one-time event driven)
- Best for: growth companies with predictable earnings trajectory

**When it DOESN'T apply**:
- Negative earnings — skip
- Negative growth — skip
- Growth > 50% (unsustainable) — apply cap at 30% for fair PE
- Turnaround situations (growth from low base) — flag

**Interpretation**:
| PEG | Signal |
|-----|--------|
| < 0.5 | Deeply undervalued (or growth overstated) |
| 0.5–0.8 | Undervalued |
| 0.8–1.2 | Fairly valued |
| 1.2–2.0 | Overvalued |
| > 2.0 | Significantly overvalued |

---

## 4. Simplified DCF

**Formula**: Enterprise Value = Σ(PV of projected FCF) + PV of Terminal Value

**Steps**:
1. Project FCF for 3 years: FCF_t = FCF_0 × (1 + g)^t, with growth deceleration
2. Terminal Value = FCF_3 × (1 + g_terminal) / (WACC - g_terminal)
3. Discount all cash flows at WACC → Enterprise Value
4. Equity Value = Enterprise Value − Net Debt (total debt − cash)
5. Per-share value = Equity Value / Shares Outstanding

**Default assumptions**:
| Parameter | Default | Range |
|-----------|---------|-------|
| WACC | 9% | 7%–12% |
| Terminal growth | 3% | 2%–4% |
| Growth rate | from revenue growth | capped 2%–30% |
| Growth deceleration | 20% per year | — |

**When it applies**:
- Positive free cash flow
- Reasonable growth rate estimate available

**When it DOESN'T apply**:
- Negative FCF — skip (can't discount negative cash flows meaningfully)
- Extreme growth assumptions — flag as speculative

**Limitations**:
- Net debt adjustment depends on yfinance `totalDebt`/`totalCash` fields; if unavailable, treated as 0 with a warning (A-shares typically lack net debt data)
- Single-stage growth model; real businesses have multiple growth phases
- WACC assumption is generic; company-specific WACC would require debt/cost of equity data

---

## 5. Graham Formula

**Formula**: V = √(22.5 × EPS × BVPS)

**Origin**: Benjamin Graham, *The Intelligent Investor*

**Where 22.5 comes from**: Maximum P/E of 15 × Maximum P/B of 1.5

**When it applies**:
- Positive EPS AND positive BVPS
- Best for: stable, mature value stocks

**When it DOESN'T apply**:
- Negative EPS or BVPS — adapted: V = EPS × 15 (if only BVPS missing)
- High-growth companies — Graham formula will systematically undervalue them
- Asset-light companies — BVPS understates true value

**Modern relevance**:
- Conservative by design — tends to give lower valuations than other models
- Useful as a "floor" for value-oriented investors
- Less applicable to tech/software companies with minimal tangible assets

---

## 6. Cross-Validation

The models rarely agree exactly. The cross-validation logic:

1. **Filter valid models**: exclude any model that skipped (null fair_price)
2. **Compute median** of all valid fair prices
3. **Flag outliers**: any model deviating >2×MAD (Median Absolute Deviation) or >30% from median is flagged as outlier
4. **Build core range**: centered on the median of core (non-outlier) models, using ±1.5×MAD as spread. The range is guaranteed to contain the median
5. **Enforce max spread**: if range >25% of midpoint, cap to ±12.5% around midpoint
6. **Rate consensus** (unified with code, core models only):
   - **🟢 Strong**: core dispersion <20%
   - **🟡 Moderate**: core dispersion 20–50%
   - **🔴 Weak**: core dispersion ≥50% or only 1 valid model

---

## 7. 3-Month Projection

The valuation models give a **fair value** — the price the stock "should" trade at.
In 3 months, we don't expect full convergence. The projection:

1. **Partial convergence**: assume 20% of the gap between current price and fair value closes in 3 months
2. **Sentiment adjustment**: shift ±0–5% based on news sentiment (clamped to [-5, +5])
3. **Volatility buffer**: apply quarterly vol buffer (annual_vol × √(3/12) = annual_vol × 0.5), then ×0.5 for half-sigma buffer. Total buffer = annual_vol × 0.25
4. **Input validation**: rejects current_price ≤ 0; auto-swaps inverted fair_range; clamps sentiment to ±5

The result is a range, not a point — because 3-month price prediction is inherently uncertain.
