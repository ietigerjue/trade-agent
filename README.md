<div align="center">

# Trade Agent / 交易助手

**Multi-Market Strategy Scanner + Stock Valuation · 多市场策略扫描 + 股票估值**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Markets](https://img.shields.io/badge/Markets-A_Share%20%7C%20US%20%7C%20Crypto-orange.svg)]()
[![Modules](https://img.shields.io/badge/Modules-Trade%20Agent%20%7C%20Stock%20Valuation-blueviolet.svg)]()

</div>

---

<div align="center">

**[English](#english)** &nbsp;|&nbsp; **[中文](#chinese)**

</div>

---

<a name="english"></a>

## 🚀 Quick Install

```bash
git clone https://github.com/ietigerjue/trade-agent.git
cd trade-agent

# --- Trade Agent: technical scanner (zero dependencies) ---
python a_share_daily_agent.py --top 8
python stock_daily_agent.py --top 8
python crypto_daily_agent.py --top 8

# --- Stock Valuation: fundamental analysis ---
pip install akshare yfinance numpy
python stock-valuation/scripts/fetch_data.py 600519 A
python stock-valuation/scripts/valuation_models.py <data_json_path>
```

### Use as Claude Code Skills

**Trade Agent** — tell Claude Code to clone and run:

```
请 clone https://github.com/ietigerjue/trade-agent 然后运行 a_share_daily_agent.py --top 8
```

**Stock Valuation** — install as a skill, then just ask naturally:

```
对茅台进行估值
What's AAPL's fair value?
```

Claude Code will auto-trigger the valuation pipeline: fetch financials → run 5 models → cross-validate → output a 3-month price target range.

---

## 📊 Overview

This repository contains two complementary stock analysis modules:

| Module | Type | What it does |
|---|---|---|
| **Trade Agent** | Technical analysis | Multi-market price-action scanner — finds trading candidates via K-line patterns, support/resistance, and momentum |
| **Stock Valuation** | Fundamental analysis | Multi-model valuation pipeline — estimates fair value via PE/PB/PEG/DCF/Graham cross-validation |

**Trade Agent** answers: *"What looks interesting right now?"*
**Stock Valuation** answers: *"What is this stock actually worth?"*

Together they provide both the tactical (entry/exit timing) and the strategic (fair value assessment) dimensions of stock analysis.

---

## 🏗️ Architecture

```
trade-agent/
├── trading_strategy.py              # Core library — data types, indicators, trade plans
├── a_share_daily_agent.py           # A-share daily scanner (most comprehensive)
├── backtest_a_share_skill.py        # Slice & rolling backtest engine
├── review_a_share_10day_signals.py  # 10-day historical signal review
├── stock_daily_agent.py             # US stock long/short pattern scanner
├── crypto_daily_agent.py            # Crypto heat & pattern analysis
├── send_a_share_report_to_lark.py   # Lark/Feishu message delivery
├── a_share_watchlist.txt            # Personal watchlist (customizable)
├── run_*.ps1                        # PowerShell launchers
├── reports/                         # Generated reports (gitignored)
│
└── stock-valuation/                 # ★ Stock valuation skill
    ├── SKILL.md                     # Skill definition (Claude Code auto-trigger)
    ├── scripts/
    │   ├── fetch_data.py            # Data fetcher (akshare + yfinance)
    │   └── valuation_models.py      # 5-model valuation engine + cross-validation
    └── references/
        └── valuation-methods.md     # Detailed methodology reference
```

---

## 📦 Module 1: Trade Agent (Technical Scanner)

Multi-market technical analysis scanner. No API keys required — all data from public endpoints.

### A-Share Daily Agent (`a_share_daily_agent.py`)

Scans all liquid Shanghai/Shenzhen main-board stocks with multiple strategies:

| Strategy | Description |
|---|---|
| **Strategy 1A** | Breakout-retest: price breaks above 30-day resistance, then retests and holds |
| **Strategy 1B** | MA30 second wave: prior run-up ≥40%, 12-55% pullback into rising MA30, bullish restart |
| **Strategy 2** | Wyckoff re-accumulation: uptrend → horizontal box → lower-edge bullish pinbar |
| **Trend Pool** | Trend-quality stocks with buy/sell ratio ≥2.0x, awaiting breakout trigger |
| **T+0 Funds** | Liquid ETFs/LOFs that support T+0 (cross-border/QDII/commodity/bond) |

```powershell
.\run_a_share_daily_agent.ps1 --top 8 --min-amount 80000000
```

### A-Share Backtest (`backtest_a_share_skill.py`)

```powershell
# Slice mode — single historical date
python backtest_a_share_skill.py --top 8 --days-ago 30 --hold-days 10

# Rolling mode — multi-date with factor attribution
python backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20

# Breakeven stop testing
python backtest_a_share_skill.py --mode rolling --top 8 --breakeven-trigger-pct 8
```

Measures: return, MFE/MAE, stop hits, target hits, HS300 excess return, factor group attribution.

### 10-Day Signal Review (`review_a_share_10day_signals.py`)

Reviews strict candidates from 10 days ago with execution discipline: signal day ≠ entry day.

```powershell
.\run_a_share_10day_signal_review.ps1 --days-ago 10
```

### US Stock Agent (`stock_daily_agent.py`)

Scans ~80 liquid US stocks via Nasdaq public API. Ranks by long/short pattern scores.

```powershell
.\run_stock_daily_agent.ps1 --top 8
```

### Crypto Agent (`crypto_daily_agent.py`)

Coinbase public endpoints. Heat score + multi-timeframe trade plans (15m/1h/4h).

```powershell
.\run_crypto_daily_agent.ps1 --top 8
```

### Core Library (`trading_strategy.py`)

Pure Python, stdlib only: `Candle` / `TradePlan` dataclasses, EMA/MACD/RSI/SMA, support/resistance detection, candlestick patterns (engulfing, morning star, piercing, double top), MACD divergence, higher-low detection, risk/reward calculation, confidence-scored trade plan builder.

---

## 📦 Module 2: Stock Valuation (Fundamental Analysis)

A rigorous multi-model valuation pipeline. Install as a Claude Code skill and trigger with natural language, or run the scripts directly.

### How it works

```
User: "对茅台进行估值"
  │
  ├─ Phase 1: Identify stock (code + market)
  ├─ Phase 2: Parallel data gathering
  │   ├─ Financial data (akshare / yfinance)
  │   ├─ News sentiment (WebSearch)
  │   └─ Industry context (WebSearch)
  ├─ Phase 3: Run 5 valuation models in parallel
  │   ├─ PE Relative  → fair price = EPS × industry median PE
  │   ├─ PB Relative  → fair price = BPS × industry median PB
  │   ├─ PEG          → fair PE = earnings growth rate
  │   ├─ DCF Simplified → 3yr FCF projection + terminal value
  │   └─ Graham       → V = √(22.5 × EPS × BVPS)
  ├─ Phase 4: Cross-validate
  │   ├─ Outlier detection (2×MAD or >30% from median)
  │   ├─ Core range (median ±1.5×MAD, capped at ±25%)
  │   ├─ Sentiment adjustment (±5%)
  │   └─ 3-month projection (20% convergence + buffer)
  └─ Output: Structured valuation report
```

### Quick start (standalone)

```bash
pip install akshare yfinance numpy

# Fetch financial data
python stock-valuation/scripts/fetch_data.py 600519 A    # 茅台
python stock-valuation/scripts/fetch_data.py AAPL US     # Apple

# Run valuation models
python stock-valuation/scripts/valuation_models.py <data_json_path>
```

The valuation engine outputs a JSON with individual model results, cross-validation stats, and a 3-month price target range (`target_low` / `target_high`).

### Quick start (Claude Code skill)

Install `stock-valuation/` as a Claude Code skill, then just ask naturally. The skill auto-triggers on phrases like "估值", "目标价", "fair value", "target price", "undervalued/overvalued".

### Models

| Model | Formula | Best for | Limitations |
|---|---|---|---|
| **PE Relative** | Fair Price = EPS × Industry Median PE | Mature, profitable companies | Skip if negative EPS |
| **PB Relative** | Fair Price = BPS × Industry Median PB | Financials, asset-heavy | Skip if no industry PB |
| **PEG** | Fair PE = Earnings Growth Rate | Growth companies | Skip if negative growth |
| **DCF Simplified** | 3yr FCF + Terminal Value − Net Debt | Any with positive FCF | Sensitive to WACC/g assumptions |
| **Graham** | V = √(22.5 × EPS × BVPS) | Value stocks | Often conservative for growth |

Models that don't apply are skipped with a reason. Results are cross-validated: outliers (>2×MAD from median) are flagged and excluded from the core range.

### Consensus strength

| Strength | Model spread | Meaning |
|---|---|---|
| 🟢 Strong | < 20% | Models agree well — higher confidence |
| 🟡 Moderate | 20–50% | Reasonable disagreement — use with caution |
| 🔴 Weak | ≥ 50% | Models diverge significantly — low confidence |

---

## ⚠️ Disclaimer

This project is for **research and educational purposes only**. It does NOT constitute financial advice. All strategy signals and valuation outputs are based on historical data and public information — they do not predict future performance. Trading involves risk. Invest responsibly.

---

<div align="center">

**Market research automation — not financial advice or an execution bot.**

</div>

---

<a name="chinese"></a>

## 🚀 安装使用

```bash
git clone https://github.com/ietigerjue/trade-agent.git
cd trade-agent

# --- Trade Agent：技术扫描器（零依赖）---
python a_share_daily_agent.py --top 8
python stock_daily_agent.py --top 8
python crypto_daily_agent.py --top 8

# --- Stock Valuation：基本面估值 ---
pip install akshare yfinance numpy
python stock-valuation/scripts/fetch_data.py 600519 A
python stock-valuation/scripts/valuation_models.py <data_json_path>
```

### 作为 Claude Code Skill 使用

**Trade Agent** — 告诉 Claude Code 克隆并运行：

```
请 clone https://github.com/ietigerjue/trade-agent 然后运行 a_share_daily_agent.py --top 8
```

**Stock Valuation** — 安装为 skill 后直接用自然语言提问：

```
对茅台进行估值
AAPL 现在高估还是低估？
```

Claude Code 会自动触发估值流程：爬取财报 → 运行5个模型 → 交叉验证 → 输出三个月目标价区间。

---

## 📊 概述

本仓库包含两个互补的股票分析模块：

| 模块 | 类型 | 功能 |
|---|---|---|
| **Trade Agent** | 技术分析 | 多市场裸K价格行为扫描 — 通过K线形态、支撑/阻力、动量寻找交易候选 |
| **Stock Valuation** | 基本面分析 | 多模型估值流程 — 通过PE/PB/PEG/DCF/Graham交叉验证估算合理价值 |

**Trade Agent** 回答：*"现在什么标的看起来有意思？"*
**Stock Valuation** 回答：*"这只股票到底值多少钱？"*

两者结合，覆盖战术层面（入场/出场时机）和战略层面（合理估值判断）。

---

## 🏗️ 项目架构

```
trade-agent/
├── trading_strategy.py              # 核心库 — 数据结构、技术指标、交易计划
├── a_share_daily_agent.py           # A股利器（最全面的扫描器）
├── backtest_a_share_skill.py        # 切片&滚动回测引擎
├── review_a_share_10day_signals.py  # 10天历史信号复盘
├── stock_daily_agent.py             # 美股多空形态扫描
├── crypto_daily_agent.py            # 加密货币热度分析
├── send_a_share_report_to_lark.py   # 飞书/Lark 报告推送
├── a_share_watchlist.txt            # 个人自选股（可自定义）
├── run_*.ps1                        # PowerShell 启动脚本
├── reports/                         # 生成的报告（已 gitignore）
│
└── stock-valuation/                 # ★ 股票估值 skill
    ├── SKILL.md                     # Skill 定义（Claude Code 自动触发）
    ├── scripts/
    │   ├── fetch_data.py            # 数据爬取（akshare + yfinance）
    │   └── valuation_models.py      # 5模型估值引擎 + 交叉验证
    └── references/
        └── valuation-methods.md     # 估值方法论参考文档
```

---

## 📦 模块一：Trade Agent（技术扫描器）

多市场技术分析扫描器。无需 API 密钥 — 所有数据来自公开接口。

### A股日线扫描器 (`a_share_daily_agent.py`)

扫描所有符合条件的沪深主板股票，多策略并行：

| 策略 | 描述 |
|---|---|
| **策略一A** | 突破后回踩：价格突破前30日压力位，回踩不破站稳 |
| **策略一B** | MA30二波回踩：前段涨幅≥40%，回撤12-55%至MA30，放量重启 |
| **策略二** | 威科夫再吸筹：上涨后横盘箱体，下沿看涨Pinbar |
| **趋势观察池** | 趋势质量达标（60日买卖盘≥2.0x），等待裸K触发 |
| **T+0基金** | 跨境/QDII/商品/债券类流动性ETF/LOF，按策略一筛选 |

```powershell
.\run_a_share_daily_agent.ps1 --top 8 --min-amount 80000000
```

### A股回测引擎 (`backtest_a_share_skill.py`)

```powershell
# 切片回测 — 单个历史日期
python backtest_a_share_skill.py --top 8 --days-ago 30 --hold-days 10

# 滚动回测 — 多信号日 + 因子归因
python backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20

# 保本止损测试
python backtest_a_share_skill.py --mode rolling --top 8 --breakeven-trigger-pct 8
```

测量指标：收益率、MFE/MAE、止损触发率、目标触达率、沪深300超额收益、因子分组归因。

### 10日信号复盘 (`review_a_share_10day_signals.py`)

复盘10天前的严格做多候选，使用与回测一致的执行纪律：信号日不买入，仅次日回踩确认后才视为进场。

```powershell
.\run_a_share_10day_signal_review.ps1 --days-ago 10
```

### 美股扫描器 (`stock_daily_agent.py`)

通过 Nasdaq 公开 API 扫描约80只高流动性美股，按多/空形态评分排序。

```powershell
.\run_stock_daily_agent.ps1 --top 8
```

### 加密货币扫描器 (`crypto_daily_agent.py`)

使用 Coinbase 公开接口。热度评分综合流动性、换手率、多周期动量和市值排名。

```powershell
.\run_crypto_daily_agent.ps1 --top 8
```

### 核心技术库 (`trading_strategy.py`)

纯 Python，仅依赖标准库：`Candle` / `TradePlan` 数据类、EMA/MACD/RSI/SMA、支撑/阻力位检测、K线形态识别（吞没、启明星、刺穿、双顶）、MACD背离、低点抬高检测、盈亏比计算、置信度评分交易计划构建器。

---

## 📦 模块二：Stock Valuation（基本面估值）

严谨的多模型估值流程。安装为 Claude Code skill 后可用自然语言触发，也可直接运行脚本。

### 工作流程

```
用户："对茅台进行估值"
  │
  ├─ 阶段1：识别股票（代码 + 市场）
  ├─ 阶段2：并行数据采集
  │   ├─ 财务数据（akshare / yfinance）
  │   ├─ 新闻情绪（WebSearch）
  │   └─ 行业背景（WebSearch）
  ├─ 阶段3：并行运行5个估值模型
  │   ├─ PE相对估值 → 合理价 = EPS × 行业中位数PE
  │   ├─ PB相对估值 → 合理价 = BPS × 行业中位数PB
  │   ├─ PEG模型    → 合理PE = 盈利增长率
  │   ├─ 简化DCF    → 3年FCF预测 + 终值
  │   └─ 格雷厄姆   → V = √(22.5 × EPS × BVPS)
  ├─ 阶段4：交叉验证
  │   ├─ 离群值检测（偏离中位数>2×MAD或>30%）
  │   ├─ 核心区间（中位数 ±1.5×MAD，区间上限±25%）
  │   ├─ 情绪修正（±5%）
  │   └─ 三个月预测（20%收敛 + 缓冲）
  └─ 输出：结构化估值报告
```

### 独立运行

```bash
pip install akshare yfinance numpy

# 获取财务数据
python stock-valuation/scripts/fetch_data.py 600519 A    # 茅台
python stock-valuation/scripts/fetch_data.py AAPL US     # 苹果

# 运行估值模型
python stock-valuation/scripts/valuation_models.py <data_json_path>
```

估值引擎输出 JSON，包含各模型结果、交叉验证统计和三个月目标价区间（`target_low` / `target_high`）。

### 模型说明

| 模型 | 公式 | 适用场景 | 局限 |
|---|---|---|---|
| **PE相对估值** | 合理价 = EPS × 行业中位数PE | 成熟盈利公司 | 亏损公司跳过 |
| **PB相对估值** | 合理价 = BPS × 行业中位数PB | 金融/重资产 | 无行业PB时跳过 |
| **PEG** | 合理PE = 盈利增长率 | 成长型公司 | 负增长跳过 |
| **简化DCF** | 3年FCF + 终值 − 净债务 | 正FCF公司 | 对WACC/g敏感 |
| **格雷厄姆** | V = √(22.5 × EPS × BVPS) | 价值股 | 对成长股偏保守 |

不适用的模型会跳过并说明原因。结果经交叉验证：偏离中位数>2×MAD的离群值被标记并排除在核心区间外。

### 一致性强度

| 强度 | 模型离散度 | 含义 |
|---|---|---|
| 🟢 强 | < 20% | 模型一致性好 — 置信度较高 |
| 🟡 中等 | 20–50% | 合理分歧 — 谨慎参考 |
| 🔴 弱 | ≥ 50% | 模型分歧大 — 置信度低 |

---

## ⚠️ 免责声明

本项目仅用于**研究和教育目的**，不构成投资建议。所有策略信号和估值结果均基于历史数据和公开信息，不代表未来收益。交易有风险，入市需谨慎。

---

<div align="center">

**这是市场研究自动化工具，不是投资建议或自动交易机器人。**

</div>
