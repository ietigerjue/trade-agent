# Trade Agent

This workspace currently contains a lightweight daily cryptocurrency heat and
pattern-analysis agent.

## Daily Crypto Agent

Run:

```powershell
.\run_crypto_daily_agent.ps1
```

The agent writes:

- `reports/crypto_daily_YYYY-MM-DD.md`
- `reports/latest.md`

It uses Coinbase public market endpoints and does not require API keys.

## What It Measures

The heat score combines:

- 24h volume and volume-to-market-cap turnover
- 1h, 24h, and 7d momentum
- Market-cap-rank quality filter

The pattern scan estimates:

- SMA20 and SMA50 trend state
- MACD direction
- RSI14 overbought/oversold state
- 20-day breakout or breakdown proximity
- 7d versus 30d volume expansion
- Annualized realized volatility

This is market research automation, not financial advice or an execution bot.

## Daily US Stock Long/Short Agent

Run:

```powershell
.\run_stock_daily_agent.ps1
```

The agent writes:

- `reports/stock_daily_YYYY-MM-DD.md`
- `reports/stock_latest.md`

It scans a default liquid US stock universe and recommends watchlist candidates
whose chart structures look more suitable for long or short ideas.

Long and short reasons use:

- 20/50/100-day moving-average structure
- 20-day and 55-day breakout or breakdown levels
- MACD direction
- RSI
- 5-day momentum
- Volume versus the 20-day average

## Daily A-Share Price-Action Agent

Run:

```powershell
.\run_a_share_daily_agent.ps1
```

The agent writes:

- `reports/a_share_daily_YYYY-MM-DD.md`
- `reports/a_share_latest.md`

It scans Shanghai/Shenzhen main-board A shares and builds a long watchlist
around your bare-K strategy:

- Recent breakout above the prior 30-day resistance, followed by a retest that
  holds above the former resistance line
- A valid retest must touch near the former resistance, close back above it,
  and print either a bullish close or bullish pinbar
- Strategy 1B separately catches strong second-wave setups: a prior run-up of
  at least 40%, a 12%-55% pullback into rising MA30 support, and a bullish
  restart after the MA30 retest holds
- The moving-average hard gate is lighter: MA20 and MA30 must be rising, and
  the close must be above MA20
- Bullish engulfing, morning star, successful retest near the breakout line, or
  MA30 second-wave restart
- Structure-stop reward/risk to the next overhead resistance or conservative
  measured-move target; strict candidates require reward/risk greater than
  `1.0`, and the structure-stop risk must not exceed 5%
- Reward/risk confidence is shown as a separate trade-space quality field:
  roughly RR 1.0 = 50%, RR 1.5 = 65%, RR 2.0 = 78%, and RR 3.0+ = 90%+
- Candidate ranking prioritizes bare-K retest/MA30 second-wave structure and bullish
  K-line confirmation before trend, sector, and news context
- Signal-day breakouts are treated as watchlist setups, not strict candidates;
  strict candidates appear only after price retests near the breakout/support
  line or rising MA30 and holds above it
- Al Brooks-style right-side context is used as a confidence/risk layer: strong
  trend context, breakout follow-through, and second retest holds add
  confidence; heavy bar overlap, trading-range upper-edge first breakouts,
  weak breakout bars, and quick moves back below resistance add risk
- Sector strength contributes to confidence and ranking: strong industry or
  concept resonance adds bullish confidence, while weak sector context adds
  bearish risk; it is not a hard entry filter
- Eastmoney Guba community discussion is a low-weight sentiment layer:
  active discussion can slightly lift confidence/ranking, while suspected
  hype or lure-style wording adds a weak risk note and small ranking drag; it
  is not a hard entry filter
- 30-day gain velocity and 60-day gain
- 60-day buy/sell pressure, using daily up-candle volume divided by daily
  down-candle volume as a stable automation proxy; default threshold is `2.0x`
- Bullish confidence boosts when price breaks above MA30 on expanded volume, or
  retests MA30 without breaking and prints a bullish pinbar
- Bullish confidence boosts when price breaks resistance with expanded volume
- MACD bullish/bearish divergence, M-top, and multiple-top structures adjust
  bullish confidence and bearish risk; default high-confidence gate is `75%`
- Bottom reversal structures such as double bottom, triple bottom, inverse
  head-and-shoulders, rounding bottom, and V-bottom right-side confirmation
  add bullish confidence
- Strategy 2 adds a separate Wyckoff re-accumulation watch section: after a
  prior uptrend, find stocks consolidating in a recent 10-day horizontal range whose latest
  candle is near the lower range boundary and forms a bullish pinbar
- A separate sector T+0 fund watch section scans liquid listed funds whose
  names suggest usually T+0-eligible categories such as cross-border/QDII,
  commodity, bond, and money-market ETF/LOF products. It applies the same
  Strategy 1 bare-K breakout screen, but keeps the results separate from
  individual-stock candidates and treats T+0 eligibility as something to
  verify in the broker trading rules.
- Rolling backtests track factor groups such as rounding bottoms, volume-backed
  breakouts, retest-hold candles, and neckline breakouts. Treat factor
  attribution as research context unless a reweighting change improves the
  rolling report.
- Broad-market index context, industry/concept strength, and lightweight
  company announcement/news notes

The report separates strict entry triggers from a trend watch pool. The trend
pool is for stocks with good 30/60-day trend quality and buy-side pressure that
still need a breakout, retest, or bullish candlestick trigger before matching
the trading plan.

You can also maintain `a_share_watchlist.txt` with one stock per line:

```text
002674 兴业科技
600584 长电科技
```

The daily A-share report adds a separate watchlist strategy check. It reuses
the same entry rules, flags stocks as matching, near-trigger, trend watch, or
not triggered, and does not change the main-board market scan.

To generate the report and send it to Feishu/Lark:

```powershell
.\run_a_share_daily_and_send_lark.ps1
```

Configure one of these before sending:

- `LARK_WEBHOOK_URL` or `FEISHU_WEBHOOK_URL` for a custom bot webhook
- `LARK_CHAT_ID` or `LARK_USER_ID` with `lark-cli` available on `PATH`

Optional: set `LARK_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_SECRET` for signed custom
bot webhooks, or `LARK_SEND_AS=user` when sending through `lark-cli` as a user.

To backtest the current A-share skill on a historical slice:

```powershell
python .\backtest_a_share_skill.py --top 8 --days-ago 30 --hold-days 10
```

This selects the highest bullish-confidence strict long candidates as of the
historical slice date, then measures their close-to-close return after the
requested number of trading days.

For rolling validation across many signal dates:

```powershell
python .\backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20
```

The rolling report treats the breakout signal day as a setup day, then only
enters if price retests near the breakout line and holds above it. It adds
5/10/20-day returns, relative HS300 return, MFE/MAE, structure-stop hits,
pressure-target hits, and factor grouping.

Optional breakeven-stop testing:

```powershell
python .\backtest_a_share_skill.py --mode rolling --top 8 --lookback-days 365 --sample-step 5 --hold-days-list 5,10,20 --breakeven-trigger-pct 8
```

The 2026-05-25 rolling tests did not support enabling breakeven stops by
default: 3%, 5%, and 8% breakeven triggers all reduced average returns versus
the no-breakeven baseline.
