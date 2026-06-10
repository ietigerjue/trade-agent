<div align="center">

# Trade Agent / 交易助手

**Multi-Market Strategy Scanner · 多市场策略扫描器**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Markets](https://img.shields.io/badge/Markets-A_Share%20%7C%20US%20%7C%20Crypto-orange.svg)]()

</div>

---

<div align="center">

**[English](#english)** &nbsp;|&nbsp; **[中文](#chinese)**

</div>

---

<a name="english"></a>

## 📊 Overview

Trade Agent is a multi-market technical analysis scanner that generates daily strategy reports for A-shares (Shanghai/Shenzhen), US stocks, and cryptocurrencies. No API keys required — all data comes from public endpoints.

**Core philosophy**: Scan the entire market, apply strict price-action rules, rank candidates by confidence, and produce structured Markdown reports. This is research automation, not financial advice or an execution bot.

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
└── reports/                         # Generated reports (gitignored)
```

## 📦 Agents

### A-Share Daily Agent (`a_share_daily_agent.py`) 

Scans all liquid Shanghai/Shenzhen main-board stocks with multiple strategies:

| Strategy | Description |
|---|---|
| **Strategy 1A** | Breakout-retest: price breaks above 30-day resistance, then retests and holds above the former resistance line |
| **Strategy 1B** | MA30 second wave: prior run-up ≥40%, 12-55% pullback into rising MA30, bullish restart after retest holds |
| **Strategy 2** | Wyckoff re-accumulation: uptrend → horizontal box → lower-edge bullish pinbar |
| **Trend Pool** | Trend-quality stocks requiring buy/sell ratio ≥2.0x, awaiting breakout trigger |
| **T+0 Funds** | Liquid ETFs/LOFs that typically support T+0 (cross-border/QDII/commodity/bond) |

**Confidence layers**:
- Price action (engulfing, morning star, pinbar, breakout bar quality)
- Al Brooks-style context (trend strength, bar overlap, breakout follow-through)
- MACD divergence (bullish/bearish)
- Reversal structures (double/triple bottom, inverse H&S, rounding bottom, V-bottom)
- Sector/industry strength resonance
- Community sentiment (Eastmoney Guba discussion analysis)

```powershell
.\run_a_share_daily_agent.ps1 --top 8 --min-amount 80000000
```

### A-Share Backtest (`backtest_a_share_skill.py`)

Two modes:

```powershell
# Slice mode — single historical date
python .\backtest_a_share_skill.py --top 8 --days-ago 30 --hold-days 10

# Rolling mode — multiple signal dates with factor attribution
python .\backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20

# Breakeven stop testing
python .\backtest_a_share_skill.py --mode rolling --top 8 --breakeven-trigger-pct 8
```

Measures: return, MFE/MAE, structure stop hits, breakeven stop hits, target hits, HS300 excess return, factor group attribution.

### 10-Day Signal Review (`review_a_share_10day_signals.py`)

Reviews strict candidates from 10 days ago using the same execution discipline as the backtest: signal day is NOT an entry; entry only after next-session retest confirmation.

```powershell
.\run_a_share_10day_signal_review.ps1 --days-ago 10
```

### US Stock Agent (`stock_daily_agent.py`)

Scans ~80 liquid US stocks via Nasdaq public API. Ranks by long/short pattern scores using moving-average structure, breakout levels, MACD, RSI, momentum, and volume.

```powershell
.\run_stock_daily_agent.ps1 --top 8
```

### Crypto Agent (`crypto_daily_agent.py`)

Uses Coinbase public endpoints. Heat score combines liquidity, turnover, multi-timeframe momentum, and market-cap rank. Generates 15m/1h/4h trade plans.

```powershell
.\run_crypto_daily_agent.ps1 --top 8
```

### Lark/Feishu Report Delivery

```powershell
.\run_a_share_daily_and_send_lark.ps1
```

Set `LARK_WEBHOOK_URL` or `LARK_CHAT_ID` before use.

## 🔧 Core Library (`trading_strategy.py`)

Pure Python, no dependencies beyond stdlib:

- `Candle` / `TradePlan` data classes (frozen, type-safe)
- EMA, MACD, RSI, SMA indicators
- Support/resistance detection
- Candlestick patterns: bullish/bearish engulfing, morning star, bearish piercing, double/multiple top
- MACD divergence (bullish/bearish)
- Higher low detection
- Risk/reward calculation
- Confidence-scored trade plan builder

---

<a name="chinese"></a>

## 📊 概述

Trade Agent 是一个多市场技术分析扫描器，每日自动生成 A 股（沪深主板）、美股和加密货币的策略报告。无需 API 密钥——所有数据来自公开接口。

**核心理念**：全市场扫描，严格按价格行为规则筛选，按置信度排序，产出结构化 Markdown 报告。这是研究自动化工具，不是投资建议或自动交易机器人。

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
└── reports/                         # 生成的报告（已 gitignore）
```

## 📦 各模块说明

### A股日线扫描器 (`a_share_daily_agent.py`)

扫描所有符合条件的沪深主板股票，多策略并行：

| 策略 | 描述 |
|---|---|
| **策略一A** | 突破后回踩：价格突破前30日压力位，回踩不破站稳 |
| **策略一B** | MA30二波回踩：前段涨幅≥40%，回撤12-55%至MA30，放量重启 |
| **策略二** | 威科夫再吸筹：上涨后横盘箱体，下沿看涨Pinbar |
| **趋势观察池** | 趋势质量达标（60日买卖盘≥2.0x），等待裸K触发 |
| **T+0基金** | 跨境/QDII/商品/债券类流动性ETF/LOF，按策略一筛选 |

**置信度层次**：
- 价格行为（吞没、启明星、Pinbar、突破K线质量）
- Al Brooks 上下文（趋势强度、K线重叠率、突破跟进）
- MACD背离（底背离/顶背离）
- 反转结构（双底/三重底/头肩底/圆弧底/V底）
- 板块/行业强度共振
- 社区情绪（东方财富股吧讨论分析）

```powershell
.\run_a_share_daily_agent.ps1 --top 8 --min-amount 80000000
```

### A股回测引擎 (`backtest_a_share_skill.py`)

两种模式：

```powershell
# 切片回测 — 单个历史日期
python .\backtest_a_share_skill.py --top 8 --days-ago 30 --hold-days 10

# 滚动回测 — 多信号日 + 因子归因
python .\backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20

# 保本止损测试
python .\backtest_a_share_skill.py --mode rolling --top 8 --breakeven-trigger-pct 8
```

测量指标：收益率、MFE/MAE、结构止损触发率、保本止损触发率、目标触达率、沪深300超额收益、因子分组归因。

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

使用 Coinbase 公开接口。热度评分综合流动性、换手率、多周期动量和市值排名。生成 15分钟/1小时/4小时交易计划。

```powershell
.\run_crypto_daily_agent.ps1 --top 8
```

### 飞书/Lark 报告推送

```powershell
.\run_a_share_daily_and_send_lark.ps1
```

使用前需配置 `LARK_WEBHOOK_URL` 或 `LARK_CHAT_ID`。

## 🔧 核心技术库 (`trading_strategy.py`)

纯 Python，仅依赖标准库：

- `Candle` / `TradePlan` 数据类（不可变、类型安全）
- EMA、MACD、RSI、SMA 技术指标
- 支撑/阻力位检测
- K线形态：看涨/看跌吞没、启明星、看跌刺穿、双顶/多重顶
- MACD 背离（底背离/顶背离）
- 低点抬高检测
- 盈亏比计算
- 置信度评分的交易计划构建器

## ⚠️ 免责声明

本项目仅用于研究和教育目的。不构成投资建议。所有策略信号均为历史数据的技术形态分析，不代表未来收益。交易有风险，入市需谨慎。

---

<div align="center">

**This is market research automation, not financial advice or an execution bot.**

**这是市场研究自动化工具，不是投资建议或自动交易机器人。**

</div>
