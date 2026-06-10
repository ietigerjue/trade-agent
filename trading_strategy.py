from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["long", "short"]


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class TradePlan:
    side: Side
    entry: float
    stop: float
    target: float
    support: float
    resistance: float
    risk_reward: float
    confidence: int
    decision: str
    setup: str
    reasons: list[str]
    add_plan: str


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def macd_series(closes: list[float]) -> list[float]:
    values: list[float] = []
    for index in range(len(closes)):
        window = closes[: index + 1]
        ema_12 = ema(window, 12)
        ema_26 = ema(window, 26)
        if ema_12 is not None and ema_26 is not None:
            values.append(ema_12 - ema_26)
        else:
            values.append(0.0)
    return values


def support_resistance(candles: list[Candle], lookback: int = 40) -> tuple[float, float]:
    window = candles[-lookback:] if len(candles) >= lookback else candles
    if not window:
        return 0.0, 0.0
    support = min(candle.low for candle in window)
    resistance = max(candle.high for candle in window)
    return support, resistance


def bullish_engulfing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    return (
        previous.close < previous.open
        and current.close > current.open
        and current.open <= previous.close
        and current.close >= previous.open
    )


def bearish_engulfing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    return (
        previous.close > previous.open
        and current.close < current.open
        and current.open >= previous.close
        and current.close <= previous.open
    )


def morning_star(candles: list[Candle]) -> bool:
    if len(candles) < 3:
        return False
    first, second, third = candles[-3], candles[-2], candles[-1]
    first_body = abs(first.close - first.open)
    second_body = abs(second.close - second.open)
    midpoint = (first.open + first.close) / 2
    return first.close < first.open and second_body < first_body * 0.45 and third.close > midpoint


def bearish_piercing(candles: list[Candle]) -> bool:
    if len(candles) < 2:
        return False
    previous, current = candles[-2], candles[-1]
    midpoint = (previous.open + previous.close) / 2
    return previous.close > previous.open and current.close < current.open and current.close < midpoint


def double_top(candles: list[Candle], tolerance: float = 0.015) -> bool:
    if len(candles) < 12:
        return False
    highs = [candle.high for candle in candles[-40:]]
    top = max(highs)
    near_tops = [high for high in highs if abs(high - top) / top <= tolerance]
    return len(near_tops) >= 2 and candles[-1].close < top * (1 - tolerance)


def multiple_top(candles: list[Candle], tolerance: float = 0.02) -> bool:
    if len(candles) < 20:
        return False
    highs = [candle.high for candle in candles[-60:]]
    top = max(highs)
    touches = sum(1 for high in highs if abs(high - top) / top <= tolerance)
    return touches >= 3 and candles[-1].close < top * (1 - tolerance / 2)


def higher_low(candles: list[Candle]) -> bool:
    if len(candles) < 12:
        return False
    lows = [candle.low for candle in candles[-12:]]
    return min(lows[-4:]) > min(lows[:4])


def macd_divergence(candles: list[Candle], side: Side) -> bool:
    if len(candles) < 35:
        return False
    closes = [candle.close for candle in candles]
    macd = macd_series(closes)
    recent = candles[-20:]
    midpoint = len(recent) // 2
    first_half = recent[:midpoint]
    second_half = recent[midpoint:]
    first_macd = macd[-20:-10]
    second_macd = macd[-10:]

    if side == "long":
        return min(candle.low for candle in second_half) < min(candle.low for candle in first_half) and min(second_macd) > min(first_macd)
    return max(candle.high for candle in second_half) > max(candle.high for candle in first_half) and max(second_macd) < max(first_macd)


def near_level(price: float, level: float, tolerance: float = 0.015) -> bool:
    if level <= 0:
        return False
    return abs(price - level) / level <= tolerance


def risk_reward(entry: float, stop: float, target: float, side: Side) -> float:
    if side == "long":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    if risk <= 0:
        return 0.0
    return reward / risk


def build_trade_plan(candles: list[Candle], side: Side, confidence_threshold: int = 70) -> TradePlan | None:
    if len(candles) < 30:
        return None

    entry = candles[-1].close
    support, resistance = support_resistance(candles)
    stop = entry * 0.95 if side == "long" else entry * 1.05
    target = resistance if side == "long" else support
    rr = risk_reward(entry, stop, target, side)

    confidence = 35
    reasons: list[str] = []
    setup = "观察"

    if side == "long":
        broke_resistance = entry >= resistance * 0.995
        held_support = near_level(entry, support, 0.035) and entry > support
        if broke_resistance:
            confidence += 18
            reasons.append("突破或贴近压力位")
            setup = "突破压力位做多"
        if held_support:
            confidence += 14
            reasons.append("靠近支撑位且未跌破")
            setup = "支撑不破做多"
        if bullish_engulfing(candles):
            confidence += 14
            reasons.append("出现看涨吞没")
        if morning_star(candles):
            confidence += 14
            reasons.append("出现启明星结构")
        if higher_low(candles):
            confidence += 8
            reasons.append("回踩低点抬高")
        if macd_divergence(candles, "long"):
            confidence += 16
            reasons.append("MACD底背离增加看涨置信度")
        add_plan = "回踩压力线且不破位，或从支撑位反弹后回踩低点抬高时加仓"
    else:
        failed_breakout = near_level(entry, resistance, 0.025) and entry < resistance
        if failed_breakout:
            confidence += 18
            reasons.append("无法有效突破压力位")
            setup = "压力位受阻做空"
        if double_top(candles):
            confidence += 16
            reasons.append("出现对子顶/M字顶")
        if multiple_top(candles):
            confidence += 18
            reasons.append("出现多重顶")
        if bearish_piercing(candles):
            confidence += 12
            reasons.append("出现看跌刺穿")
        if bearish_engulfing(candles):
            confidence += 14
            reasons.append("出现看跌吞没")
        if macd_divergence(candles, "short"):
            confidence += 16
            reasons.append("MACD顶背离增加看跌置信度")
        add_plan = "做空策略不主动加仓；若反弹再次受阻压力位，可重新评估新仓"

    if rr > 2 and confidence >= confidence_threshold:
        decision = "可交易"
    elif rr > 2:
        decision = "观察-置信度不足"
    else:
        decision = "不交易-盈亏比不足"

    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        support=support,
        resistance=resistance,
        risk_reward=rr,
        confidence=min(confidence, 100),
        decision=decision,
        setup=setup,
        reasons=reasons or ["形态信号不足"],
        add_plan=add_plan,
    )
